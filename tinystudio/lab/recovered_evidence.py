"""Project-authorized attachment of operator-supplied bytes and scoped lineage.

Recovery never renews runner authority, changes execution state, qualifies a
runtime or physical outcome, parses observations, or registers a model. Callers
own HTTP/project authorization and commit the append-only publication.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import tempfile
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from tinystudio.storage import StorageError
from . import service as svc
from .contracts import canonical, checksum
from .models import LabArtifact, LabAttempt, LabEvent, LabTarget


FORMAT = 'tinystudio.operator-recovered-evidence.v1'
EVENT_KIND = 'server.evidence_recovered'
CLASSIFICATION = 'operator_recovered_evidence'
_SHA = r'^[0-9a-f]{64}$'


class _Exact(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class RecoveryArtifactRef(_Exact):
    artifact_id: str = Field(min_length=1, max_length=64)
    sha256: str = Field(pattern=_SHA)
    size: int = Field(ge=0)


class RecoveryFileRef(RecoveryArtifactRef):
    name: str = Field(min_length=1)


class RecoveryPreviewRequest(_Exact):
    source_attempt_id: str = Field(min_length=1, max_length=64)
    target_id: str = Field(min_length=1, max_length=64)
    spec_digest: str = Field(pattern=_SHA)
    manifest: RecoveryArtifactRef
    files: list[RecoveryFileRef]


class RecoveryAttachRequest(RecoveryPreviewRequest):
    preview_digest: str = Field(pattern=_SHA)
    idempotency_key: str = Field(min_length=1, max_length=120)


@dataclass(frozen=True)
class RecoveryParserLimits:
    """An optional host memory budget, without a scientific file/count limit."""
    manifest_bytes: int | None = None

    def __post_init__(self):
        if self.manifest_bytes is not None and (type(self.manifest_bytes) is not int or self.manifest_bytes < 0):
            raise ValueError('manifest_bytes must be a nonnegative host byte budget or None')

    @classmethod
    def from_environment(cls):
        value = os.environ.get('TINYSTUDIO_RECOVERY_MANIFEST_BYTES', '').strip()
        return cls(None if not value else int(value))


class _Origin(_Exact):
    server: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    spec_digest: str = Field(pattern=_SHA)
    engine: str = Field(min_length=1)
    implementation_digest: str = Field(pattern=_SHA)
    runtime: dict[str, str]

    @model_validator(mode='after')
    def public_origin(self):
        parsed = urlsplit(self.server)
        _ = parsed.port
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment or parsed.path
                or any(ord(char) < 32 for char in self.server)):
            raise ValueError('spool origin requires an HTTP(S) server without credentials, path, query or fragment')
        return self


class _Outcome(_Exact):
    verification: Literal['unverified']
    physical_stop: Literal['unverified']


class _Cleanup(_Exact):
    status: Literal['unknown', 'confirmed']
    private_context_removed: bool


class _Ack(_Exact):
    accepted: bool
    state: Literal['succeeded', 'failed', 'cancelled']
    run_id: str
    attempt_id: str


class _Publication(_Exact):
    status: Literal['unknown', 'server_acknowledged']
    server_state: Literal['succeeded', 'failed', 'cancelled'] | None = None
    ack: _Ack | None = None

    @model_validator(mode='after')
    def acknowledgement(self):
        if self.status == 'unknown':
            if self.server_state is not None or self.ack is not None:
                raise ValueError('unknown publication cannot contain an acknowledgement')
        elif self.ack is None or self.ack.accepted is not True or self.ack.state != self.server_state:
            raise ValueError('spool publication acknowledgement is inconsistent')
        return self


class _Policy(_Exact):
    retention: Literal['keep'] | int | float

    @model_validator(mode='after')
    def retention_seconds(self):
        if self.retention != 'keep' and (isinstance(self.retention, bool) or not math.isfinite(self.retention) or self.retention < 0):
            raise ValueError('retention must be keep or finite nonnegative seconds')
        return self


class _Quota(_Exact):
    max_bytes: int | None = Field(ge=0)
    used_bytes: int = Field(ge=0)
    exceeded: bool


class _JsonlFacts(_Exact):
    newline_count: int = Field(ge=0)
    tail_bytes: int = Field(ge=0)
    ends_with_newline: bool
    semantic_validation: Literal['not_performed']


class _ManifestFile(_Exact):
    path: str
    size: int = Field(ge=0)
    sha256: str = Field(pattern=_SHA)
    jsonl: _JsonlFacts | None = None


class _SpoolManifest(_Exact):
    format: Literal['tinystudio.evidence-spool.v1']
    origin: _Origin
    classification: Literal['operator_host_evidence']
    outcome: _Outcome
    cleanup: _Cleanup
    publication: _Publication
    policy: _Policy
    quota: _Quota
    files: list[_ManifestFile]


def validate_recovery_path(name):
    """Validate a logical evidence identity, independently of upload labels.

    Logical POSIX paths preserve colon components and their full depth. They are
    never extracted to host paths here; archive import streams identified bytes
    into CAS artifacts whose separate upload labels retain their own contract.
    """
    if (not isinstance(name, str) or not name.startswith('evidence/')
            or PurePosixPath(name).is_absolute() or '\\' in name
            or any(part in {'', '.', '..'} for part in name.split('/'))
            or any(ord(char) < 32 or ord(char) == 127 for char in name)):
        raise svc.LabError('recovered evidence requires a relative POSIX path inside evidence/ without traversal', 422)
    return name


def _request(model, body):
    try:
        request = model.model_validate(body.model_dump() if isinstance(body, BaseModel) else body).model_dump()
        names = []
        for item in request['files']:
            validate_recovery_path(item['name'])
            names.append(item['name'])
        if len(names) != len(set(names)):
            raise ValueError('duplicate recovered file names')
        if 'idempotency_key' in request and not request['idempotency_key'].strip():
            raise ValueError('recovery idempotency key must be nonempty')
        request['files'].sort(key=lambda item: item['name'])
        return request
    except (ValueError, TypeError, OverflowError) as exc:
        raise svc.LabError(str(exc), 422) from exc


def _source(db, project, run_id, request, *, lock=False):
    run = svc.get_run(db, project, run_id, lock=lock)
    attempt = db.scalar(select(LabAttempt).where(LabAttempt.id == request['source_attempt_id'],
                        LabAttempt.run_id == run.id).execution_options(populate_existing=True))
    target = db.scalar(select(LabTarget).where(LabTarget.id == request['target_id'], LabTarget.project_id == project))
    if attempt is None:
        raise svc.LabError('source attempt not found for this run', 404)
    if target is None or run.target_id != target.id or attempt.target_id != target.id:
        raise svc.LabError('source target differs from run or attempt identity', 409)
    if request['spec_digest'] != run.spec_digest or checksum(run.spec) != run.spec_digest:
        raise svc.LabError('source run specification checksum mismatch', 409)
    if (not isinstance(run.spec, dict) or not isinstance(run.spec.get('definition'), dict)
            or not isinstance(run.spec['definition'].get('engine'), str)
            or not isinstance(run.spec.get('implementation'), dict) or not isinstance(run.spec.get('runtime'), dict)):
        raise svc.LabError('source run specification lacks required frozen identities', 409)
    return run, attempt


def _identity(row):
    return tuple(getattr(row, key) for key in ('id', 'project_id', 'attempt_id', 'name', 'sha256', 'size',
                                               'media_type', 'role', 'visibility', 'storage_key'))


def _artifact(db, project, ref, attempt_id, *, manifest=False):
    row = svc.get_artifact(db, project, ref['artifact_id'])
    if row.attempt_id is not None and (manifest or row.attempt_id != attempt_id):
        raise svc.LabError('recovered artifact belongs to a different attempt or manifest attribution', 409)
    if row.sha256 != ref['sha256'] or row.size != ref['size']:
        raise svc.LabError('recovered artifact checksum or size differs from requested identity', 409)
    return row


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate spool manifest JSON key')
        value[key] = item
    return value


def _manifest(path, row, limits):
    if limits.manifest_bytes is not None and row.size > limits.manifest_bytes:
        raise svc.LabError('spool manifest exceeds the configured host parser byte budget', 413)
    with path.open('rb') as stream:
        content = stream.read() if limits.manifest_bytes is None else stream.read(limits.manifest_bytes + 1)
    if limits.manifest_bytes is not None and len(content) > limits.manifest_bytes:
        raise svc.LabError('spool manifest exceeds the configured host parser byte budget', 413)
    def nonfinite(value):
        raise ValueError('nonfinite spool manifest JSON number: ' + value)
    parsed = json.loads(content, object_pairs_hook=_unique, parse_constant=nonfinite)
    _SpoolManifest.model_validate(parsed)
    # Validation must not normalize producer claims, omit explicit null budgets,
    # or add absent optional fields to the retained manifest representation.
    manifest = parsed
    if manifest['publication']['status'] == 'unknown' and set(manifest['publication']) != {'status'}:
        raise ValueError('unknown spool publication contains acknowledgement fields')
    names = []
    for item in manifest['files']:
        validate_recovery_path(item['path'])
        if 'jsonl' in item and item['jsonl'] is None:
            raise ValueError('declared JSONL byte facts require an explicit object')
        names.append(item['path'])
    if len(names) != len(set(names)):
        raise ValueError('duplicate spool manifest file path')
    return manifest


def _byte_facts(path, jsonl):
    digest, size, newlines, tail, ends = hashlib.sha256(), 0, 0, 0, False
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 ** 2), b''):
            digest.update(block)
            size += len(block)
            newlines += block.count(b'\n')
            tail = len(block.rsplit(b'\n', 1)[-1]) if b'\n' in block else tail + len(block)
            ends = block.endswith(b'\n')
    result = {'sha256': digest.hexdigest(), 'size': size}
    if jsonl:
        result['jsonl'] = {'newline_count': newlines, 'tail_bytes': tail, 'ends_with_newline': ends,
                           'semantic_validation': 'not_performed'}
    return result


def _qualification():
    return {'scope': 'bytes_and_existing_server_identity_only', 'operator_supplied': True,
            'scientific_truth': False, 'physical_completion': False, 'runtime_attestation_verified': False,
            'cleanup_and_outcome': 'producer_claims_only', 'execution_authority_granted': False}


@contextmanager
def _prepared(db, project, run_id, request, limits):
    run, attempt = _source(db, project, run_id, request)
    source_states = {'run': run.state, 'attempt': attempt.state}
    source_states_observed_at = svc.now().isoformat()
    manifest_row = _artifact(db, project, request['manifest'], attempt.id, manifest=True)
    if limits.manifest_bytes is not None and manifest_row.size > limits.manifest_bytes:
        raise svc.LabError('spool manifest exceeds the configured host parser byte budget', 413)
    identities = {manifest_row.id: _identity(manifest_row)}
    try:
        with tempfile.TemporaryDirectory(prefix='ts-recovered-evidence-') as root:
            directory = Path(root)
            manifest = _manifest(svc.materialize(manifest_row, directory), manifest_row, limits)
            origin = manifest['origin']
            expected = {'run_id': run.id, 'attempt_id': attempt.id, 'target_id': run.target_id,
                        'spec_digest': run.spec_digest, 'engine': run.spec['definition']['engine'],
                        'implementation_digest': checksum(run.spec['implementation']), 'runtime': run.spec['runtime']}
            if any(origin[key] != value for key, value in expected.items()):
                raise svc.LabError('spool origin differs from the existing server run/attempt specification', 409)
            if manifest['publication']['status'] == 'server_acknowledged':
                ack = manifest['publication']['ack']
                if ack['run_id'] != run.id or ack['attempt_id'] != attempt.id:
                    raise svc.LabError('producer acknowledgement refers to a different run or attempt', 409)
            declared = {item['path']: item for item in manifest['files']}
            if set(declared) != {item['name'] for item in request['files']}:
                raise svc.LabError('uploaded recovered files do not exactly cover the spool manifest', 409)
            files = []
            for ref in request['files']:
                item = declared[ref['name']]
                if item['sha256'] != ref['sha256'] or item['size'] != ref['size']:
                    raise svc.LabError('manifest file checksum or size differs from uploaded file identity', 409)
                artifact = _artifact(db, project, ref, attempt.id)
                identities[artifact.id] = _identity(artifact)
                path = svc.materialize(artifact, directory)
                facts = _byte_facts(path, 'jsonl' in item or Path(item['path']).suffix.lower() in {'.jsonl', '.ndjson'})
                if (facts['sha256'], facts['size']) != (artifact.sha256, artifact.size):
                    raise svc.LabError('recovered bytes changed during verification', 409)
                if 'jsonl' in item and facts['jsonl'] != item['jsonl']:
                    raise svc.LabError('recovered JSONL newline/tail byte facts differ from the manifest', 409)
                files.append({'name': item['path'], 'artifact': svc.artifact_dict(artifact),
                              'attempt_id': artifact.attempt_id, 'verified_bytes': facts})
            prepared = {'format': FORMAT, 'classification': CLASSIFICATION,
                        'source': {'project_id': project, 'run_id': run.id, 'attempt_id': attempt.id,
                                   'target_id': run.target_id, 'spec_digest': run.spec_digest},
                        'source_states': source_states, 'source_states_observed_at': source_states_observed_at,
                        'manifest': svc.artifact_dict(manifest_row), 'files': files,
                        'producer_manifest': manifest, 'qualification': _qualification(),
                        'parser_policy': {'manifest_in_memory': True, 'max_manifest_bytes': limits.manifest_bytes}}
            prepared['digest'] = checksum({key: value for key, value in prepared.items()
                                           if key not in {'source_states', 'source_states_observed_at'}})
            yield prepared, identities, directory
    except svc.LabError:
        raise
    except StorageError as exc:
        raise svc.LabError('recovered artifact bytes are unavailable or could not be verified', 409) from exc
    except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError) as exc:
        raise svc.LabError(str(exc), 422) from exc


def preview_recovered_evidence(db, project, run_id, body, *, limits=None):
    request = _request(RecoveryPreviewRequest, body)
    limits = limits or RecoveryParserLimits.from_environment()
    with _prepared(db, project, run_id, request, limits) as (prepared, *_):
        return prepared


def _previous(db, run_id, key, digest):
    previous = db.scalar(select(LabEvent).where(LabEvent.run_id == run_id, LabEvent.kind == EVENT_KIND,
                         LabEvent.payload['idempotency_key'].as_string() == key))
    if previous is None:
        return None
    payload = previous.payload
    if payload.get('request_digest') != digest or payload.get('classification') != CLASSIFICATION:
        raise svc.LabError('recovery key was already used for different inputs', 409)
    return {**deepcopy(payload), 'replayed': True}


def attach_recovered_evidence(db, project, run_id, body, author_id, *, limits=None):
    request = _request(RecoveryAttachRequest, body)
    digest = checksum(request)
    svc.get_run(db, project, run_id)
    previous = _previous(db, run_id, request['idempotency_key'], digest)
    if previous is not None:
        return previous
    limits = limits or RecoveryParserLimits.from_environment()
    with _prepared(db, project, run_id, request, limits) as (prepared, identities, directory):
        if prepared['digest'] != request['preview_digest']:
            raise svc.LabError('recovered evidence preview changed; inspect a new preview before attachment', 409)
        document = {**prepared, 'author_id': author_id, 'request_digest': digest,
                    'idempotency_key': request['idempotency_key'], 'verified_at': svc.now().isoformat(),
                    'origin': {'kind': CLASSIFICATION, 'server_record': {'run_id': run_id, 'kind': EVENT_KIND,
                               'idempotency_key': request['idempotency_key']}}}
        path = directory / 'recovered-evidence-receipt.json'
        path.write_bytes(canonical(document) + b'\n')
        verified = svc.persist_verified(path, project, 'application/json')
        with db.begin_nested():
            run, attempt = _source(db, project, run_id, request, lock=True)
            previous = _previous(db, run_id, request['idempotency_key'], digest)
            if previous is not None:
                return previous
            for identity in identities.values():
                artifact = db.scalar(select(LabArtifact).where(LabArtifact.id == identity[0],
                                     LabArtifact.project_id == project).with_for_update()
                                     .execution_options(populate_existing=True))
                if artifact is None or _identity(artifact) != identity:
                    raise svc.LabError('recovered artifact metadata changed after byte verification', 409)
            receipt = svc.store_artifact(db, project, path, path.name, 'application/json', CLASSIFICATION,
                                         visibility='evaluation', verified=verified)
            payload = {'classification': CLASSIFICATION, 'idempotency_key': request['idempotency_key'],
                       'request_digest': digest, 'author_id': author_id, 'source_attempt_id': attempt.id,
                       'source_states': {'run': run.state, 'attempt': attempt.state},
                       'manifest_artifact_id': prepared['manifest']['id'], 'manifest_sha256': prepared['manifest']['sha256'],
                       'receipt': svc.artifact_dict(receipt)}
            svc.event(db, run, EVENT_KIND, payload)
            db.flush()
            return {**deepcopy(payload), 'replayed': False}


def recovery_references(db, run_id):
    """Server-reserved associations; caller has already authorized the run."""
    return [deepcopy(row.payload) for row in db.scalars(select(LabEvent).where(
        LabEvent.run_id == run_id, LabEvent.kind == EVENT_KIND).order_by(LabEvent.seq))]
