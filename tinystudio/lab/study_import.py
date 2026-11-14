"""Verified, explicit artifact projections into the append-only study ledger.

    Preview is deterministic and writes no study records. Apply pins that exact
    preview, publishes all observations and its receipt atomically, and replays
    a successful idempotency key without reopening external source objects.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from tinystudio.storage import StorageError
from . import service as svc, studies
from .contracts import canonical, checksum
from .models import LabArtifact, LabStudyRecord
from .study_contracts import MAX_EXACT_INTEGER, StudyRecordRequest, finite_document, finite_numbers
from .study_projection import StudyProjection
from .study_import_paths import StudyImportKeys


FORMAT = 'tinystudio.study-observation-import.v1'


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    artifact_id: str = Field(min_length=1, max_length=64, strict=True)
    revision: int = Field(ge=1, le=MAX_EXACT_INTEGER, strict=True)
    projection: dict


class ImportRequest(PreviewRequest):
    preview_digest: str = Field(pattern=r'^[0-9a-f]{64}$', strict=True)
    idempotency_key: str = Field(min_length=1, max_length=120, strict=True)


def _validate(model, body):
    try:
        validated = model.model_validate(body)
        finite_document(validated.model_dump())
        value = validated.model_dump(mode='json')
        if 'idempotency_key' in value and not value['idempotency_key'].strip():
            raise ValueError('import idempotency key must be nonempty')
        finite_document(value)
        projection = StudyProjection.from_dict(value['projection'])
        value['projection'] = projection.to_dict()
        return value, projection
    except (ValueError, TypeError, OverflowError) as error:
        raise svc.LabError(str(error), 422) from error


def _implementation_sha256():
    root, digest = Path(__file__).resolve().parent, hashlib.sha256()
    for name in ('study_import.py', 'study_import_paths.py', 'study_projection.py', 'json_selection.py',
                 'studies.py', 'study_contracts.py', 'contracts.py'):
        digest.update(name.encode() + b'\0' + (root / name).read_bytes())
    return digest.hexdigest()


def _identity(artifact):
    return artifact.id, artifact.storage_key, artifact.sha256, artifact.size


def _file_identity(path):
    digest, size = hashlib.sha256(), 0
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 ** 2), b''):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


@contextmanager
def _prepared(db, project, study_id, request, projection):
    study = studies._study(db, project, study_id)
    protocol = studies._revision(db, study, request['revision'])
    artifact = svc.get_artifact(db, project, request['artifact_id'])
    identity = _identity(artifact)
    try:
        with tempfile.TemporaryDirectory(prefix='ts-study-import-') as directory:
            path = svc.materialize(artifact, Path(directory))
            if _file_identity(path) != (artifact.sha256, artifact.size):
                raise svc.LabError('observation source checksum or size mismatch', 409)
            summary = projection.stream_summary(path)
            source = {name: getattr(artifact, name) for name in ('id', 'sha256', 'size', 'role', 'visibility')}
            prepared = {'format': FORMAT, 'study_id': study_id, 'revision': request['revision'],
                        'protocol_digest': protocol.digest, 'source': source,
                        'projection': projection.to_dict(), 'summary': summary,
                        'implementation_sha256': _implementation_sha256()}
            # Preview examples are a display aid, not an admission budget for
            # the selected cohort. Each actual ledger record retains its normal
            # portable document validation at publication.
            finite_numbers(prepared)
            prepared['digest'] = checksum(prepared)
            # Materialization above is the verification; avoid downloading the
            # same bulk object for every individual observation reference.
            verified = {(artifact.storage_key, artifact.sha256, artifact.size): svc.now().isoformat()}
            reference = studies._artifact_reference(db, project, artifact.id, verified)
            yield prepared, path, identity, reference
    except svc.LabError:
        raise
    except StorageError as error:
        raise svc.LabError('observation source bytes are unavailable or could not be verified', 409) from error
    except (ValueError, TypeError, OverflowError, UnicodeError) as error:
        raise svc.LabError(str(error), 422) from error


def preview_import(db, project, study_id, body):
    request, projection = _validate(PreviewRequest, body)
    with _prepared(db, project, study_id, request, projection) as (prepared, *_):
        return prepared


def _marker_key(key):
    return StudyImportKeys(key).receipt


def _previous(db, study, key, intent_digest):
    previous = db.scalar(select(LabStudyRecord).where(LabStudyRecord.study_id == study.id,
                         LabStudyRecord.idempotency_key == _marker_key(key)))
    if previous is None:
        return None
    receipt = studies._record(db, study, previous)
    data = receipt.get('data', {})
    if receipt['kind'] != 'observation_import' or data.get('format') != FORMAT or data.get('intent_digest') != intent_digest:
        raise svc.LabError('observation import key was already used for different inputs', 409)
    return _result(receipt, replayed=True)


def _result(receipt, *, replayed):
    data = receipt['data']
    return {'receipt': receipt, 'count': data['count'], 'first_sequence': data['first_sequence'],
            'last_sequence': data['last_sequence'], 'replayed': replayed}


def apply_import(db, project, study_id, body, author_id):
    request, projection = _validate(ImportRequest, body)
    intent_digest = checksum({'request': request, 'author_id': author_id})
    study = studies._study(db, project, study_id)
    previous = _previous(db, study, request['idempotency_key'], intent_digest)
    if previous is not None:
        return previous
    with _prepared(db, project, study_id, request, projection) as (prepared, path, identity, reference):
        if prepared['digest'] != request['preview_digest']:
            raise svc.LabError('observation import preview changed; inspect a new preview before importing', 409)
        # Savepoint protects callers who catch an error and continue their own
        # transaction. A failed bulk publication cannot leave a partial cohort.
        with db.begin_nested():
            study = studies._study(db, project, study_id, lock=True)
            previous = _previous(db, study, request['idempotency_key'], intent_digest)
            if previous is not None:
                return previous
            artifact = db.scalar(select(LabArtifact).where(LabArtifact.id == request['artifact_id'],
                                 LabArtifact.project_id == project).with_for_update()
                                 .execution_options(populate_existing=True))
            if artifact is None or _identity(artifact) != identity:
                raise svc.LabError('observation source identity changed after verification', 409)
            if studies._revision(db, study, request['revision']).digest != prepared['protocol_digest']:
                raise svc.LabError('observation import protocol changed', 409)
            first, count, digest = study.next_seq + 1, 0, hashlib.sha256()
            for projected in projection.iter_rows(path):
                row = projected.to_dict()
                encoded = canonical(row)
                digest.update(len(encoded).to_bytes(8, 'big'))
                digest.update(encoded)
                data = deepcopy(row['data'])
                data['_source_projection'] = {'artifact_id': artifact.id, 'artifact_sha256': artifact.sha256,
                    'line': row['line'], 'raw_line_sha256': row['raw_line_sha256'],
                    'decoded_row_sha256': row['decoded_row_sha256'], 'projection_digest': prepared['digest']}
                record = studies._validate(StudyRecordRequest, {
                    'revision': request['revision'], 'idempotency_key': StudyImportKeys(request['idempotency_key']).row(row['line']),
                    **{name: row[name] for name in ('kind', 'unit_id', 'condition', 'observed_at')},
                    'data': data, 'artifact_ids': [artifact.id]})
                studies._append_verified_record(db, project, study_id, record, author_id, [], [reference])
                count += 1
            if (count != prepared['summary']['count'] or digest.hexdigest() != prepared['summary']['rows_sha256']
                    or _file_identity(path) != (artifact.sha256, artifact.size)):
                raise svc.LabError('observation source or projection changed during publication', 409)
            retained_preview = {**prepared, 'summary': {
                key: value for key, value in prepared['summary'].items() if key != 'preview'}}
            data = {'format': FORMAT, 'intent_digest': intent_digest, 'preview': retained_preview,
                    'count': count, 'first_sequence': first if count else None,
                    'last_sequence': study.next_seq if count else None}
            marker = studies._validate(StudyRecordRequest, {'revision': request['revision'],
                'idempotency_key': _marker_key(request['idempotency_key']), 'kind': 'observation_import',
                'data': data, 'artifact_ids': [artifact.id]})
            receipt = studies._append_verified_record(db, project, study_id, marker, author_id, [], [reference])
            return _result(receipt, replayed=False)
