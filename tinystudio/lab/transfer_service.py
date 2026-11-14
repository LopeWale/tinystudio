"""Durable multipart intent, reconciliation and fenced artifact publication.

Admissions are committed by the API. Control methods commit phase transitions
before provider calls; callers must enter without unrelated pending writes and
commit returned state before ACK. Only a transfer row is locked during small
provider controls. Streaming verification takes no target/run locks, and its
artifact association shares the operation worker's fenced finish transaction.
"""
from copy import deepcopy
import re

from sqlalchemy import func, select
from sqlalchemy.orm import object_session

from tinystudio.db.models import Project, new_id
from tinystudio.storage import storage
from . import service as svc
from .command_profiles import _json
from .contracts import checksum
from .models import LabArtifact, LabAttempt, LabOperation, LabRun, LabTarget, LabTransfer
from .transfer_configuration import (MultipartGeometry, MultipartPlan, TransferBudget, TransferBudgetExceeded,
                                     TransferPaths, TransferPolicy)


_AUTHORITY_FIELDS = {'run_id', 'attempt_id', 'target_id', 'token_hash', 'target_token_hash', 'spec_digest'}
_INACTIVE = {'aborted'}


def _authority(value):
    if value is None:
        return None
    if (type(value) is not dict or set(value) != _AUTHORITY_FIELDS
            or any(type(value[key]) is not str or not value[key] for key in value)
            or any(re.fullmatch('[0-9a-f]{64}', value[key]) is None
                   for key in ('token_hash', 'target_token_hash', 'spec_digest'))):
        raise svc.LabError('invalid derived attempt authority')
    return deepcopy(value)


def attempt_identity(run, attempt, target):
    if attempt.run_id != run.id or attempt.target_id != target.id or run.target_id != target.id:
        raise svc.LabError('attempt identity does not match its run and target', 403)
    return _authority({'run_id': run.id, 'attempt_id': attempt.id, 'target_id': target.id,
                       'token_hash': attempt.token_hash, 'target_token_hash': target.token_hash,
                       'spec_digest': run.spec_digest})


def require_scope(row, attempt_authority=None):
    if row.attempt_identity != _authority(attempt_authority):
        raise svc.LabError('transfer is outside the authenticated input or attempt scope', 403)


def _identity(row):
    return {'id': row.id, 'project_id': row.project_id, 'idempotency_key': row.idempotency_key,
            'intent': row.intent, 'attempt_identity': row.attempt_identity, 'policy': row.policy,
            'geometry': row.geometry, 'storage_key': row.storage_key, 'size': row.size}


def _integrity(row):
    if checksum(_identity(row)) != row.request_digest or row.size != row.intent['size']:
        raise svc.LabError('immutable transfer identity changed', 409)
    if row.storage_key != TransferPaths(row.project_id, row.id).object_key:
        raise svc.LabError('transfer storage identity changed', 409)


def get_transfer(db, project, id_, lock=False):
    query = select(LabTransfer).where(LabTransfer.id == id_, LabTransfer.project_id == project)
    if lock:
        query = query.with_for_update()
    row = db.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise svc.LabError('transfer not found', 404)
    _integrity(row)
    return row


def transfer_dict(row, db=None):
    db = db if db is not None else object_session(row)
    operation = db.get(LabOperation, row.operation_id) if db is not None and row.operation_id else None
    if operation is not None:
        db.refresh(operation)
    return {'id': row.id, 'state': row.state, 'intent': deepcopy(row.intent), 'plan': deepcopy(row.geometry),
            'request_digest': row.request_digest, 'operation_id': row.operation_id,
            'operation_state': operation.state if operation is not None else None,
            'artifact': deepcopy(row.artifact_receipt), 'error_class': row.error_class,
            'created': row.created.isoformat() if row.created else None,
            'updated': row.updated.isoformat() if row.updated else None}


def _policy(row):
    value = row.policy
    return TransferPolicy(per_file=TransferBudget(**value['per_file']), aggregate=TransferBudget(**value['aggregate']),
        geometry=MultipartGeometry(part_bytes=value['geometry']['part_bytes'], concurrency=value['geometry']['concurrency']),
        signed_url_expiry_seconds=value['signed_url_expiry_seconds'],
        verifier_timeout_seconds=value['verifier_timeout_seconds'])


def _make_store(policy):
    from .transfer_storage import MultipartArtifactStore
    return MultipartArtifactStore(storage(), policy)


def _project_lock(db, project):
    if db.scalar(select(Project.id).where(Project.id == project).with_for_update()) is None:
        raise svc.LabError('project not found', 404)


def _live(db, project, identity, *, lock=False):
    if identity is None:
        return None, None
    def read(model, condition):
        query = select(model).where(condition)
        if lock:
            query = query.with_for_update()
        return db.scalar(query.execution_options(populate_existing=True))
    # All publication/admission paths use project -> target -> run -> attempt.
    target = read(LabTarget, LabTarget.id == identity['target_id'])
    run = read(LabRun, LabRun.id == identity['run_id'])
    attempt = read(LabAttempt, LabAttempt.id == identity['attempt_id'])
    if (target is None or run is None or attempt is None or target.project_id != project or run.project_id != project
            or not target.enabled or target.token_hash != identity['target_token_hash']
            or run.target_id != target.id or attempt.target_id != target.id or attempt.run_id != run.id
            or attempt.token_hash != identity['token_hash'] or run.spec_digest != identity['spec_digest']
            or checksum(run.spec) != identity['spec_digest'] or run.state != 'running' or attempt.state != 'running'
            or svc.aware(attempt.lease_until) <= svc.now()):
        raise svc.LabError('transfer no longer has fresh matching attempt authority', 409)
    # A cancellation request can still retain explicit cancelled/failed audit
    # evidence. Existing finish() forbids reporting success after cancellation.
    return run, attempt


def _intent(value, output):
    if (type(value) is not dict or not {'name', 'size', 'sha256'} <= value.keys()
            or value.keys() - {'name', 'size', 'sha256', 'media_type', 'role', 'visibility'}):
        raise svc.LabError('transfer intent has missing or unsupported fields')
    try:
        _json(value)
    except ValueError:
        raise svc.LabError('transfer intent requires finite JSON') from None
    result = {'media_type': 'application/octet-stream', 'role': 'evidence' if output else 'source',
              'visibility': 'model', **deepcopy(value)}
    svc.validate_artifact_name(result['name'])
    if type(result['size']) is not int or result['size'] < 0 or type(result['sha256']) is not str or re.fullmatch('[0-9a-f]{64}', result['sha256']) is None:
        raise svc.LabError('transfer requires an exact size and SHA-256 identity')
    for name, maximum in (('media_type', 120), ('role', 64)):
        if (type(result[name]) is not str or not result[name] or len(result[name]) > maximum
                or any(ord(character) < 32 or ord(character) == 127 for character in result[name])):
            raise svc.LabError('invalid transfer ' + name)
    if type(result['visibility']) is not str or result['visibility'] not in {'model', 'evaluation', 'truth'}:
        raise svc.LabError('invalid transfer visibility')
    return result


def _matching(artifact, intent):
    return all(getattr(artifact, key) == intent[key] for key in ('name', 'size', 'sha256', 'media_type', 'role', 'visibility'))


def _output_budget(db, run, attempt, size, *, exclude=None):
    maximum = run.spec.get('definition', {}).get('execution', {}).get('max_output_bytes')
    if maximum is None:
        return
    if type(maximum) is not int or maximum < 0:
        raise svc.LabError('invalid frozen output budget', 409)
    used = db.scalar(select(func.coalesce(func.sum(LabArtifact.size), 0)).where(LabArtifact.attempt_id == attempt.id))
    pending = list(db.scalars(select(LabTransfer).where(LabTransfer.project_id == run.project_id,
        LabTransfer.artifact_id.is_(None), LabTransfer.state != 'aborted')))
    used += sum(item.size for item in pending if item.id != exclude
                and item.attempt_identity is not None and item.attempt_identity['attempt_id'] == attempt.id)
    if used + size > maximum:
        raise svc.LabError('attempt output budget exceeded', 413)


def _aggregate(db, project, budget, size, artifact_id=None):
    if budget.max_bytes is None and budget.max_files is None:
        return
    # Scope: this project's non-aborted transfer reservations. Active sessions
    # each reserve their declared bytes; verified aliases charge an artifact once.
    reserved = {}
    for row in db.scalars(select(LabTransfer).where(LabTransfer.project_id == project, LabTransfer.state != 'aborted')):
        key = ('artifact', row.artifact_id) if row.artifact_id else ('transfer', row.id)
        reserved[key] = row.size
    if artifact_id is None or ('artifact', artifact_id) not in reserved:
        reserved[('new', '')] = size
    budget.check_total(sum(reserved.values()), len(reserved))


def admit(db, project, intent, key, *, attempt_authority=None, policy=None):
    identity = _authority(attempt_authority)
    selected = _intent(intent, identity is not None)
    if type(key) is not str or not key.strip() or len(key) > 120:
        raise svc.LabError('transfer idempotency key must contain 1–120 characters')
    _project_lock(db, project)
    previous = db.scalar(select(LabTransfer).where(LabTransfer.project_id == project, LabTransfer.idempotency_key == key))
    if previous is not None:
        _integrity(previous)
        if previous.intent != selected or previous.attempt_identity != identity:
            raise svc.LabError('transfer idempotency key belongs to a different immutable request', 409)
        return previous
    policy = TransferPolicy.from_environment() if policy is None else policy
    if not isinstance(policy, TransferPolicy):
        raise svc.LabError('transfer requires a TransferPolicy')
    run, attempt = _live(db, project, identity, lock=True)
    existing = None
    if attempt is not None:
        existing = db.scalar(select(LabArtifact).where(LabArtifact.attempt_id == attempt.id, LabArtifact.name == selected['name']))
        if existing is not None and not _matching(existing, selected):
            raise svc.LabError('output name already has different immutable artifact metadata', 409)
        if existing is None:
            _output_budget(db, run, attempt, selected['size'])
    try:
        policy.per_file.check_size(selected['size'])
        _aggregate(db, project, policy.aggregate, selected['size'], existing.id if existing else None)
        plan = policy.geometry.plan(selected['size'])
    except TransferBudgetExceeded as error:
        raise svc.LabError(str(error), 413) from None
    except ValueError as error:
        raise svc.LabError(str(error), 413) from None
    identifier = new_id()
    row = LabTransfer(id=identifier, project_id=project, idempotency_key=key, intent=selected,
        attempt_identity=identity, policy=policy.as_dict(), geometry=plan.as_dict(), size=selected['size'],
        storage_key=TransferPaths(project, identifier).object_key, state='verified' if existing else 'admitted',
        parts=[], artifact_id=existing.id if existing else None,
        artifact_receipt=svc.artifact_dict(existing) if existing else None)
    row.request_digest = checksum(_identity(row))
    db.add(row); db.flush()
    return row


def _set(row, state, error=None):
    row.state, row.updated = state, svc.now()
    row.error_class = type(error).__name__[:120] if error is not None else None


def _queue_verify(db, project, id_):
    # This is separate from the provider-control transaction: never take the
    # project admission lock while holding a transfer lock from provider I/O.
    _project_lock(db, project)
    row = get_transfer(db, project, id_, lock=True)
    if row.state in {'verified', 'verifying', 'aborted', 'aborting', 'abort_uncertain'}:
        return row
    if row.state != 'sealed':
        raise svc.LabError('transfer object is not sealed', 409)
    _live(db, project, row.attempt_identity)
    from .operations import _request
    operation = _request(db, project, 'artifact_verify', {'transfer_id': row.id, 'request_digest': row.request_digest},
                         'transfer-verify:' + row.id)
    row.operation_id = operation.id
    _set(row, 'verifying')
    db.flush()
    return row


def initialize(db, project, id_, store):
    row = get_transfer(db, project, id_, lock=True)
    if row.state in {'verified', 'verifying', 'aborted', 'aborting', 'abort_uncertain'}:
        return row
    if row.state == 'sealed':
        db.commit()
        return _queue_verify(db, project, id_)
    if row.state not in {'admitted', 'initializing', 'initialization_uncertain'}:
        return row
    _live(db, project, row.attempt_identity)
    fresh = row.state == 'admitted'
    if fresh:
        _set(row, 'initializing'); db.commit()
    row = get_transfer(db, project, id_, lock=True)
    if row.state not in {'initializing', 'initialization_uncertain'}:
        return row
    _live(db, project, row.attempt_identity)
    try:
        if store.head(row.storage_key) is not None:
            _set(row, 'sealed')
        elif row.geometry['zero']:
            _set(row, 'uploading')
        else:
            uploads = store.inspect_uploads(row.storage_key)
            if len(uploads) == 1:
                row.upload_id = uploads[0]; _set(row, 'uploading')
            elif not uploads and fresh:
                row.upload_id = store.begin(row.storage_key, row.intent['media_type']); _set(row, 'uploading')
            else:
                _set(row, 'initialization_uncertain')
    except Exception as error:
        _set(row, 'initialization_uncertain', error)
    db.commit()
    if row.state == 'sealed':
        return _queue_verify(db, project, id_)
    return row


def sign_part(db, project, id_, number, store):
    row = get_transfer(db, project, id_, lock=True)
    if row.state != 'uploading' or not row.upload_id:
        raise svc.LabError('transfer is not open for multipart signing', 409)
    _live(db, project, row.attempt_identity)
    try:
        part = MultipartPlan(row.size, row.geometry['part_bytes']).part(number)
    except ValueError as error:
        raise svc.LabError(str(error)) from None
    descriptor = store.sign_part(row.storage_key, row.upload_id, number, part['size'],
                                 expiry=row.policy['signed_url_expiry_seconds'])
    _live(db, project, row.attempt_identity)
    return {**deepcopy(descriptor), 'part_number': number, **part}


def _parts(row, values):
    if type(values) is not list:
        raise svc.LabError('invalid provider multipart inventory', 409)
    plan = MultipartPlan(row.size, row.geometry['part_bytes'])
    if len(values) != plan.part_count:
        raise svc.LabError('multipart inventory is incomplete', 409)
    result = []
    for number, value in enumerate(values, 1):
        expected = plan.part(number)
        if (type(value) is not dict or type(value.get('number')) is not int or value['number'] != number
                or type(value.get('size')) is not int or value['size'] != expected['size']
                or type(value.get('etag')) is not str or not value['etag']):
            raise svc.LabError('multipart inventory differs from the frozen geometry', 409)
        result.append({'number': number, 'size': value['size'], 'etag': value['etag']})
    return result


def complete(db, project, id_, store):
    row = get_transfer(db, project, id_, lock=True)
    if row.state in {'verified', 'verifying', 'aborted', 'aborting', 'abort_uncertain'}:
        return row
    if row.state == 'sealed':
        db.commit()
        return _queue_verify(db, project, id_)
    _live(db, project, row.attempt_identity)
    fresh = row.state == 'uploading'
    if fresh:
        row.parts = [] if row.geometry['zero'] else _parts(row, store.parts(row.storage_key, row.upload_id))
        _set(row, 'sealing'); db.commit()
    elif row.state not in {'sealing', 'completion_uncertain'}:
        raise svc.LabError('transfer must be initialized before completion', 409)
    row = get_transfer(db, project, id_, lock=True)
    if row.state not in {'sealing', 'completion_uncertain'}:
        return row
    _live(db, project, row.attempt_identity)
    try:
        if store.head(row.storage_key) is not None:
            _set(row, 'sealed')
        elif fresh:
            if row.geometry['zero']:
                store.put_empty(row.storage_key, row.intent['media_type'])
            else:
                store.complete(row.storage_key, row.upload_id, deepcopy(row.parts))
            _set(row, 'sealed')
        else:
            # An ambiguous completion is never replayed with a refreshed ETag
            # list. Missing-object reconciliation requires explicit operator action.
            _set(row, 'completion_uncertain')
    except Exception as error:
        _set(row, 'completion_uncertain', error)
    db.commit()
    if row.state == 'sealed':
        return _queue_verify(db, project, id_)
    return row


def inspect(db, project, id_, store):
    row = get_transfer(db, project, id_)
    if row.state in {'initializing', 'initialization_uncertain'}:
        row = initialize(db, project, id_, store)
    elif row.state in {'sealing', 'completion_uncertain', 'sealed'}:
        row = complete(db, project, id_, store)
    if row.state == 'uploading' and row.upload_id:
        row = get_transfer(db, project, id_, lock=True)
        _live(db, project, row.attempt_identity)
        values = store.parts(row.storage_key, row.upload_id)
        # Partial inventories are exposed as provider observations, never as a
        # complete publication or an invitation to silently refresh a sealed list.
        return {'transfer': transfer_dict(row, db), 'parts': deepcopy(values)}
    return {'transfer': transfer_dict(row, db), 'parts': deepcopy(row.parts)}


def abort(db, project, id_, store, *, project_authority=False):
    # Match deletion/publication lock order; release this project lock with the
    # durable abort intent before performing any provider control calls.
    _project_lock(db, project)
    row = get_transfer(db, project, id_, lock=True)
    if row.state in {'verified', 'aborted'}:
        return row
    if project_authority is not True:
        _live(db, project, row.attempt_identity)
    never_initialized = row.state == 'admitted'
    if row.operation_id:
        from .operations import cancel_operation
        cancel_operation(db, project, row.operation_id)
    _set(row, 'aborting'); db.commit()
    row = get_transfer(db, project, id_, lock=True)
    if row.state != 'aborting':
        return row
    try:
        uploads = [] if never_initialized else [row.upload_id] if row.upload_id else store.inspect_uploads(row.storage_key)
        from .transfer_storage import TransferUploadMissing
        for identifier in uploads:
            try:
                store.abort(row.storage_key, identifier)
            except TransferUploadMissing:
                pass  # Provider proves this upload ID is no longer usable.
        _set(row, 'aborted')
    except Exception as error:
        _set(row, 'abort_uncertain', error)
    db.commit()
    return row


def execute_verify(db, project, payload, directory, check_authority):
    if type(payload) is not dict or set(payload) != {'transfer_id', 'request_digest'}:
        raise svc.LabError('invalid artifact verification operation payload', 409)
    row = get_transfer(db, project, payload['transfer_id'])
    if row.request_digest != payload['request_digest'] or row.state != 'verifying' or not row.operation_id:
        raise svc.LabError('transfer verification no longer has its immutable intent', 409)
    _live(db, project, row.attempt_identity)
    check_authority()
    identity = _make_store(_policy(row)).verify(row.storage_key, row.size, row.intent['sha256'],
                                               check_authority=check_authority)
    expected = {'storage_key': row.storage_key, 'size': row.size, 'sha256': row.intent['sha256']}
    if identity != expected:
        raise svc.LabError('verified object differs from the immutable transfer identity', 409)
    check_authority()
    _project_lock(db, project)
    run, attempt = _live(db, project, row.attempt_identity, lock=True)
    row = get_transfer(db, project, row.id, lock=True)
    if row.state != 'verifying' or row.request_digest != payload['request_digest']:
        raise svc.LabError('transfer was aborted or superseded during verification', 409)
    operation = db.scalar(select(LabOperation).where(LabOperation.id == row.operation_id).execution_options(populate_existing=True))
    if operation is None or operation.kind != 'artifact_verify' or operation.payload != payload or operation.state != 'running' or svc.aware(operation.lease_until) <= svc.now():
        raise svc.LabError('artifact verification operation authority ended', 409)
    existing = None
    if attempt is not None:
        existing = db.scalar(select(LabArtifact).where(LabArtifact.attempt_id == attempt.id, LabArtifact.name == row.intent['name']))
        if existing is not None and not _matching(existing, row.intent):
            raise svc.LabError('output name already has different immutable artifact metadata', 409)
        if existing is None:
            _output_budget(db, run, attempt, row.size, exclude=row.id)
    if existing is None:
        existing = LabArtifact(project_id=project, attempt_id=attempt.id if attempt else None,
            storage_key=row.storage_key, **deepcopy(row.intent))
        db.add(existing); db.flush()
    row.artifact_id, row.artifact_receipt = existing.id, svc.artifact_dict(existing)
    _set(row, 'verified')
    db.flush()
    # The caller's lease.finish() and transaction commit publish this artifact
    # and the verified transfer association together, or roll both back.
    return {'artifact': deepcopy(row.artifact_receipt), 'transfer_id': row.id}


def check_verify_publication_authority(db, operation, result):
    """Recheck provisional publication after the operation fence is acquired.

    execute_verify already holds the project, target, run, attempt and transfer
    locks in that order. Its artifact changes remain uncommitted; a rejection
    here rolls those changes back with the operation's publication transaction.
    """
    payload = operation.payload
    if type(payload) is not dict or set(payload) != {'transfer_id', 'request_digest'}:
        raise svc.LabError('invalid artifact verification operation payload', 409)
    row = get_transfer(db, operation.project_id, payload['transfer_id'])
    if (row.state != 'verified' or row.operation_id != operation.id
            or row.request_digest != payload['request_digest']
            or row.artifact_id is None or row.artifact_receipt is None
            or result != {'artifact': row.artifact_receipt, 'transfer_id': row.id}):
        raise svc.LabError('transfer no longer has its provisional verified association', 409)
    # Time can advance while lease.finish waits for the operation row, although
    # the frozen attempt identity rows remain locked in this transaction.
    _live(db, operation.project_id, row.attempt_identity)
