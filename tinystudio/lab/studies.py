"""Versioned protocols and append-only study records. Callers own transactions."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
from urllib.parse import urlencode

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tinystudio.db.models import Project, new_id
from tinystudio.storage import ArtifactNotFound, StorageError
from .contracts import canonical, checksum
from .models import LabAttempt, LabEvent, LabStudy, LabStudyRecord, LabStudyRevision
from . import service as svc
from .study_contracts import MAX_EXACT_INTEGER, StudyProtocol, StudyRecordRequest, finite_document


FORMAT = 'tinystudio.study-tracking.v1'


def _validate(model, value):
    try:
        return model.model_validate(value).model_dump(mode='json')
    except (ValueError, TypeError, OverflowError) as error:
        raise svc.LabError(str(error), 422) from error


def _study(db, project, id_, *, lock=False):
    query = select(LabStudy).where(LabStudy.id == id_, LabStudy.project_id == project).execution_options(populate_existing=True)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise svc.LabError('study not found', 404)
    return row


def _revision(db, row, revision):
    stored = db.get(LabStudyRevision, (row.id, revision), populate_existing=True)
    if stored is None:
        raise svc.LabError('study protocol revision not found', 404)
    snapshot = stored.snapshot
    if checksum(snapshot) != stored.digest or snapshot.get('study_id') != row.id or snapshot.get('revision') != revision:
        raise svc.LabError('study protocol checksum mismatch', 409)
    return stored


def _summary(db, id_, through_seq):
    records, units = db.execute(select(func.count(), func.count(func.distinct(func.nullif(LabStudyRecord.unit_id, ''))))
        .where(LabStudyRecord.study_id == id_, LabStudyRecord.seq <= through_seq)).one()
    return {'records': records, 'declared_unit_ids': units}


def get_study(db: Session, project: str, id_: str, *, revision: int | None = None):
    row = _study(db, project, id_)
    stored = _revision(db, row, row.revision if revision is None else revision)
    return {'id': row.id, **deepcopy(stored.snapshot), 'digest': stored.digest,
            'head_revision': row.revision, 'created': svc.aware(row.created).isoformat(),
            'updated': svc.aware(row.updated).isoformat(), 'summary': _summary(db, row.id, row.next_seq), 'last_sequence': row.next_seq}


def list_studies(db: Session, project: str, *, offset=0, limit=100):
    rows = db.scalars(select(LabStudy).where(LabStudy.project_id == project)
                      .order_by(LabStudy.created.desc(), LabStudy.id).offset(offset).limit(limit))
    result = []
    for row in rows:
        saved = _revision(db, row, row.revision)
        result.append({'id': row.id, 'name': saved.snapshot['study']['name'],
            'status': saved.snapshot['study']['status'], 'revision': row.revision, 'digest': saved.digest,
            'created': svc.aware(row.created).isoformat(), 'updated': svc.aware(row.updated).isoformat(),
            'record_count': _summary(db, row.id, row.next_seq)['records']})
    return result


def revisions(db: Session, project: str, id_: str):
    row = _study(db, project, id_)
    result = []
    for number in db.scalars(select(LabStudyRevision.revision).where(LabStudyRevision.study_id == id_).order_by(LabStudyRevision.revision.desc())):
        stored = _revision(db, row, number)
        snapshot = stored.snapshot
        result.append({'revision': number, 'digest': stored.digest, 'author_id': snapshot['author_id'],
                       'recorded_at': snapshot['recorded_at'], 'name': snapshot['study']['name'], 'status': snapshot['study']['status']})
    return result


def save_study(db: Session, project: str, document: dict, author_id: str, *, id_=None, expected=None, key=None):
    document = _validate(StudyProtocol, document)
    if id_:
        row = _study(db, project, id_, lock=True)
        _revision(db, row, row.revision)
        if type(expected) is not int or expected != row.revision:
            raise svc.LabError('study changed; reload its current protocol revision before saving', 409)
        if row.revision >= MAX_EXACT_INTEGER:
            raise svc.LabError('protocol revision exceeds exact integer representation', 409)
        row.revision += 1
    else:
        if not isinstance(key, str) or not key.strip() or len(key) > 120:
            raise svc.LabError('study creation requires an idempotency key of1–120 characters', 422)
        # Serializes creation keys without relying on a check-then-insert race.
        parent = db.scalar(select(Project).where(Project.id == project).with_for_update())
        if parent is None:
            raise svc.LabError('project not found', 404)
        request_digest = checksum({'study': document, 'author_id': author_id})
        row = db.scalar(select(LabStudy).where(LabStudy.project_id == project, LabStudy.idempotency_key == key))
        if row is not None:
            if row.request_digest != request_digest:
                raise svc.LabError('study creation key was already used for different inputs', 409)
            return get_study(db, project, row.id, revision=1)
        row = LabStudy(id=new_id(), project_id=project, name=document['name'], revision=1, next_seq=0,
                       idempotency_key=key, request_digest=request_digest)
        db.add(row)
    row.name, row.updated = document['name'], svc.now()
    snapshot = {'schema_version': 1, 'study_id': row.id, 'revision': row.revision, 'study': document,
                'author_id': author_id, 'recorded_at': svc.now().isoformat()}
    db.add(LabStudyRevision(study_id=row.id, revision=row.revision, snapshot=snapshot, digest=checksum(snapshot)))
    db.flush()
    return get_study(db, project, row.id)


def _artifact_reference(db, project, id_, verified):
    artifact = svc.get_artifact(db, project, id_)
    reference = svc.artifact_dict(artifact)
    identity = (artifact.storage_key, artifact.sha256, artifact.size)
    if identity not in verified:
        try:
            with tempfile.TemporaryDirectory(prefix='ts-study-reference-') as directory:
                svc.materialize(artifact, Path(directory))
        except ArtifactNotFound as error:
            raise svc.LabError('linked artifact bytes are unavailable; association was not recorded', 409) from error
        except StorageError as error:
            raise svc.LabError('linked artifact bytes could not be verified; association was not recorded', 409) from error
        verified[identity] = svc.now().isoformat()
    reference.update(byte_verification='sha256_and_size', verified_at=verified[identity])
    if artifact.attempt_id:
        attempt = db.get(LabAttempt, artifact.attempt_id)
        run = svc.get_run(db, project, attempt.run_id)
        reference['origin'] = {'run_id': run.id, 'attempt_id': attempt.id, 'spec_digest': run.spec_digest}
    return reference


def _references(db, project, request):
    runs = []
    verified = {}
    for id_ in sorted(request['run_ids']):
        run = svc.get_run(db, project, id_)
        if checksum(run.spec) != run.spec_digest:
            raise svc.LabError('linked run specification checksum mismatch', 409)
        snapshot = {'id': run.id, 'spec_digest': run.spec_digest, 'definition_id': run.definition_id,
                    'revision': run.revision, 'state_at_link': run.state, 'error_at_link': run.error,
                    'event_seq': run.next_seq, 'captured_at': svc.now().isoformat()}
        current_artifacts = deepcopy((run.result or {}).get('artifacts', []))
        retained_outcomes = [deepcopy(event.payload) for event in db.scalars(select(LabEvent).where(
            LabEvent.run_id == id_, LabEvent.kind == 'server.outcome', LabEvent.seq <= snapshot['event_seq']).order_by(LabEvent.seq))]
        result_artifacts = []
        for ref in current_artifacts:
            resolved = _artifact_reference(db, project, ref['id'], verified)
            if resolved['sha256'] != ref['sha256'] or resolved['size'] != ref['size']:
                raise svc.LabError('linked run artifact checksum mismatch', 409)
            if resolved.get('origin', {}).get('run_id') != id_:
                raise svc.LabError('linked artifact does not belong to its declared run', 409)
            result_artifacts.append(resolved)
        outcomes = []
        for ref in retained_outcomes:
            resolved = _artifact_reference(db, project, ref['outcome_artifact_id'], verified)
            if resolved['sha256'] != ref['sha256']:
                raise svc.LabError('linked outcome artifact checksum mismatch', 409)
            outcomes.append({**deepcopy(ref), 'artifact': resolved})
        runs.append({**snapshot, 'result_artifacts': result_artifacts, 'outcomes': outcomes})
    return runs, [_artifact_reference(db, project, id_, verified) for id_ in request['artifact_ids']]


def _record(db, study, row):
    record = row.record
    if (checksum(record) != row.digest or record.get('id') != row.id or record.get('study_id') != study.id or
            record.get('seq') != row.seq or record.get('revision') != row.revision or
            record.get('kind') != row.kind or record.get('unit_id') != row.unit_id):
        raise svc.LabError('study record checksum mismatch', 409)
    if record.get('protocol_digest') != _revision(db, study, row.revision).digest:
        raise svc.LabError('study record protocol checksum mismatch', 409)
    return {**deepcopy(record), 'digest': row.digest}


def append_record(db: Session, project: str, id_: str, body: dict, author_id: str):
    request = _validate(StudyRecordRequest, body)
    study = _study(db, project, id_)
    request_digest = checksum({'request': request, 'author_id': author_id})
    previous = db.scalar(select(LabStudyRecord).where(LabStudyRecord.study_id == id_, LabStudyRecord.idempotency_key == request['idempotency_key']))
    if previous is not None:
        if previous.request_digest != request_digest:
            raise svc.LabError('record idempotency key was already used for different inputs', 409)
        return _record(db, study, previous)
    protocol = _revision(db, study, request['revision'])
    # Prepare slow object verification without holding study/run write locks.
    # Snapshot source state before verification; later retries cannot rewrite it.
    run_refs, artifact_refs = _references(db, project, request)
    study = _study(db, project, id_, lock=True)
    previous = db.scalar(select(LabStudyRecord).where(LabStudyRecord.study_id == id_, LabStudyRecord.idempotency_key == request['idempotency_key']))
    if previous is not None:
        if previous.request_digest != request_digest:
            raise svc.LabError('record idempotency key was already used for different inputs', 409)
        return _record(db, study, previous)
    protocol = _revision(db, study, request['revision'])
    if request['supersedes'] is not None:
        superseded = db.scalar(select(LabStudyRecord).where(LabStudyRecord.id == request['supersedes'], LabStudyRecord.study_id == id_))
        if superseded is None:
            raise svc.LabError('superseded record not found in this study', 404)
        _record(db, study, superseded)
    if study.next_seq >= MAX_EXACT_INTEGER:
        raise svc.LabError('record sequence exceeds exact integer representation', 409)
    record = {'schema_version': 1, 'id': new_id(), 'study_id': id_, 'seq': study.next_seq + 1,
              'revision': request['revision'], 'protocol_digest': protocol.digest,
              **{key: request[key] for key in ('kind', 'unit_id', 'condition', 'observed_at', 'data', 'supersedes')},
              'run_references': run_refs, 'artifact_references': artifact_refs,
              'author_id': author_id, 'recorded_at': svc.now().isoformat()}
    try:
        finite_document(record)
    except (ValueError, TypeError, OverflowError) as error:
        raise svc.LabError(str(error), 422) from error
    row = LabStudyRecord(id=record['id'], study_id=id_, seq=record['seq'], revision=request['revision'],
                         kind=request['kind'], unit_id=request['unit_id'], idempotency_key=request['idempotency_key'],
                         request_digest=request_digest, record=record, digest=checksum(record))
    study.next_seq = record['seq']
    db.add(row)
    db.flush()
    return _record(db, study, row)


def records(db: Session, project: str, id_: str, *, after=0, limit=100):
    study = _study(db, project, id_)
    rows = list(db.scalars(select(LabStudyRecord).where(LabStudyRecord.study_id == id_, LabStudyRecord.seq > after,
                           LabStudyRecord.seq <= study.next_seq)
                           .order_by(LabStudyRecord.seq).limit(limit)))
    values = [_record(db, study, row) for row in rows]
    next_after = rows[-1].seq if rows else after
    return {'records': values, 'next_after': next_after, 'latest_seq': study.next_seq, 'has_more': next_after < study.next_seq}


def tracking_manifest(db: Session, project: str, id_: str, *, revision=None, through_seq=None):
    study = _study(db, project, id_)
    revision = study.revision if revision is None else revision
    through_seq = study.next_seq if through_seq is None else through_seq
    if type(revision) is not int or not 1 <= revision <= study.revision or type(through_seq) is not int or not 0 <= through_seq <= study.next_seq:
        raise svc.LabError('export boundary is outside the retained study history', 422)
    if db.scalar(select(func.count()).select_from(LabStudyRecord).where(LabStudyRecord.study_id == id_,
                    LabStudyRecord.seq <= through_seq, LabStudyRecord.revision > revision)):
        raise svc.LabError('export boundary omits a protocol revision referenced by its records', 409)
    saved = _revision(db, study, revision)
    return {'format': FORMAT, 'kind': 'manifest', 'study_id': id_, 'revision': revision, 'through_seq': through_seq,
            'protocol_digest': saved.digest, 'external_evidence_bytes': False,
            'export_path': f'/api/lab/studies/{id_}/export?'+urlencode({'revision': revision, 'through_seq': through_seq})}


def export_tracking(db: Session, project: str, id_: str, directory: Path, *, revision=None, through_seq=None):
    header = tracking_manifest(db, project, id_, revision=revision, through_seq=through_seq)
    study = _study(db, project, id_)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory/f'study-{study.id}-r{header["revision"]}-s{header["through_seq"]}.jsonl'
    sha = hashlib.sha256()
    def write(stream, document):
        line = canonical(document)+b'\n'
        stream.write(line); sha.update(line)
    # Open outside cleanup: failure must not unlink a caller's existing file.
    stream = path.open('xb')
    try:
        with stream:
            write(stream, header)
            revision_count = record_count = 0
            for number in db.scalars(select(LabStudyRevision.revision).where(LabStudyRevision.study_id == id_,
                         LabStudyRevision.revision <= header['revision']).order_by(LabStudyRevision.revision)):
                saved = _revision(db, study, number)
                write(stream, {'kind': 'protocol', 'snapshot': saved.snapshot, 'digest': saved.digest})
                revision_count += 1
            query = select(LabStudyRecord).where(LabStudyRecord.study_id == id_, LabStudyRecord.seq <= header['through_seq']).order_by(LabStudyRecord.seq)
            for row in db.scalars(query.execution_options(yield_per=100)):
                write(stream, {'kind': 'record', 'record': _record(db, study, row)})
                record_count += 1
            if record_count != header['through_seq'] or revision_count != header['revision']:
                raise svc.LabError('study tracking history is incomplete', 409)
            stream.write(canonical({'kind': 'export_summary', 'sha256': sha.hexdigest(),
                         'protocol_revisions': revision_count, 'records': record_count})+b'\n')
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise
