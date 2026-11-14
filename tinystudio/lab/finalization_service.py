"""Private finish preparation and publication behind the raw-token service gate.

Prepared values contain canonical text and derived identity only. They hold no
database transaction, temporary file, provider stream, or reusable credential.
Callers still own the publication transaction and its commit/rollback.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import secrets
import tempfile

from sqlalchemy import select
from sqlalchemy.orm import Session

from tinystudio.db.models import Project
from tinystudio.storage import project_key
from . import service as svc
from .contracts import canonical, checksum
from .models import LabArtifact, LabAttempt, LabEvent, LabOperation, LabRun, LabTarget


@dataclass(frozen=True)
class _FrozenTargetSnapshot:
    id: str
    project_id: str
    token_hash: str | None


def _snapshot_target(target: LabTarget) -> _FrozenTargetSnapshot:
    """Capture authenticated identity before a controller commits/expires ORM."""
    return _FrozenTargetSnapshot(target.id, target.project_id, target.token_hash)


@dataclass(frozen=True)
class FinishIdentity:
    project_id: str
    target_id: str
    run_id: str
    attempt_id: str
    attempt_token_hash: str
    target_token_hash: str | None
    spec_digest: str


@dataclass(frozen=True)
class FrozenPreparedFinish:
    identity: FinishIdentity
    request_json: str
    payload_digest: str
    spec_json: str
    canonical_result_json: str
    outcome_json: str
    archive_identity_json: str


def _text(value) -> str:
    return canonical(value).decode()


def _read_text(value: str):
    try:
        decoded = json.loads(value)
        if _text(decoded) != value:
            raise ValueError
        return decoded
    except (TypeError, ValueError):
        raise svc.LabError("invalid prepared finish document", 409) from None


def _target_matches(target: LabTarget | None, identity: FinishIdentity) -> None:
    if target is None or not target.enabled:
        raise svc.LabError("execution target is disabled", 403)
    if target.project_id != identity.project_id or target.token_hash != identity.target_token_hash:
        raise svc.LabError("execution target credential changed during completion verification", 403)


def _locked_authority(db: Session, identity: FinishIdentity, *, historical=False) -> tuple[LabRun, LabAttempt]:
    # Project deletion takes project before run locks. Acquire this order before
    # inserting the retained archive's project/attempt foreign-key association.
    if db.scalar(select(Project.id).where(Project.id == identity.project_id).with_for_update()) is None:
        raise svc.LabError("project not found", 404)
    target = db.scalar(select(LabTarget).where(LabTarget.id == identity.target_id)
                       .with_for_update(key_share=True).execution_options(populate_existing=True))
    _target_matches(target, identity)
    run = svc.get_run(db, identity.project_id, identity.run_id, lock=True)
    attempt = db.scalar(select(LabAttempt).where(LabAttempt.id == identity.attempt_id)
                        .with_for_update().execution_options(populate_existing=True))
    if (attempt is None or attempt.target_id != identity.target_id or attempt.run_id != identity.run_id
            or run.target_id != identity.target_id
            or not secrets.compare_digest(attempt.token_hash, identity.attempt_token_hash)):
        raise svc.LabError("invalid attempt authority", 403)
    if not historical and attempt.state == "running" and svc.aware(attempt.lease_until) <= svc.now():
        raise svc.LabError("attempt lease expired; stale execution may not publish", 409)
    return run, attempt


def _ended_ack(attempt: LabAttempt, payload_digest: str) -> dict | None:
    if attempt.state == "running":
        return None
    if attempt.result_digest == payload_digest:
        return {"accepted": True, "state": attempt.state}
    raise svc.LabError("attempt already ended with a different outcome", 409)


def _authenticate_finish(db: Session, target: LabTarget, attempt_id: str, token: str,
                         state: str, result: dict | None, error: str | None) -> tuple[FinishIdentity, str] | dict:
    """The synchronous entry gate always verifies the raw attempt credential."""
    if state not in {"succeeded", "failed", "cancelled"}:
        raise svc.LabError("invalid terminal state")
    # Capture the authenticated target before any populate_existing query can
    # refresh its ORM instance. Trusted local targets legitimately have no hash.
    project_id, target_id, target_hash = target.project_id, target.id, target.token_hash
    attempt = db.scalar(select(LabAttempt).where(LabAttempt.id == attempt_id)
                        .execution_options(populate_existing=True))
    attempt_hash = svc.secret_hash(token)
    if (attempt is None or attempt.target_id != target_id
            or not secrets.compare_digest(attempt.token_hash, attempt_hash)):
        raise svc.LabError("invalid attempt authority", 403)
    run = svc.get_run(db, project_id, attempt.run_id)
    identity = FinishIdentity(project_id, target_id, run.id, attempt.id, attempt_hash, target_hash, run.spec_digest)
    current_target = db.scalar(select(LabTarget).where(LabTarget.id == target_id)
                               .execution_options(populate_existing=True))
    _target_matches(current_target, identity)
    request = _text({"state": state, "result": result, "error": error})
    if attempt.state != "running":
        _, ended = _locked_authority(db, identity)
        replay = _ended_ack(ended, hashlib.sha256(request.encode()).hexdigest())
        if replay is not None:
            return replay
    if svc.aware(attempt.lease_until) <= svc.now():
        raise svc.LabError("attempt lease expired; stale execution may not publish", 409)
    if checksum(run.spec) != identity.spec_digest:
        raise svc.LabError("immutable specification checksum mismatch", 409)
    return identity, request


def _prepare_finish(db: Session, identity: FinishIdentity, request_json: str, *, verify_result=None) -> FrozenPreparedFinish:
    """Verify storage with no authority locks and retain exact canonical text."""
    request = _read_text(request_json)
    run = svc.get_run(db, identity.project_id, identity.run_id)
    spec_json = _text(run.spec)
    if run.spec_digest != identity.spec_digest or checksum(run.spec) != identity.spec_digest:
        raise svc.LabError("immutable specification checksum mismatch", 409)
    result = svc._canonical_result(db, identity.project_id, identity.attempt_id, request["result"])
    if request["state"] == "succeeded" and (not result or not result.get("artifacts")):
        raise svc.LabError("success requires a complete evidence manifest")
    result_json = _text(result)
    document = svc._outcome_document(run, identity.attempt_id, request["state"], result, request["error"])
    outcome_json = _text(document)
    with tempfile.TemporaryDirectory(prefix="ts-lab-outcome-") as root:
        directory = Path(root)
        (svc._verify_result if verify_result is None else verify_result)(db, identity.project_id, result, directory)
        path = directory / "attempt-outcome.json"
        path.write_bytes(outcome_json.encode())
        verified = svc.persist_verified(path, identity.project_id, "application/json")
    return FrozenPreparedFinish(identity, request_json, hashlib.sha256(request_json.encode()).hexdigest(),
                                spec_json, result_json, outcome_json, _text(verified))


def _prepared_documents(prepared: FrozenPreparedFinish) -> tuple[dict, dict | None, dict]:
    request = _read_text(prepared.request_json)
    result = _read_text(prepared.canonical_result_json)
    verified = _read_text(prepared.archive_identity_json)
    spec = _read_text(prepared.spec_json)
    identity = prepared.identity
    if (not isinstance(request, dict) or set(request) != {"state", "result", "error"}
            or request["state"] not in {"succeeded", "failed", "cancelled"}
            or checksum(request) != prepared.payload_digest or checksum(spec) != identity.spec_digest):
        raise svc.LabError("prepared finish identity mismatch", 409)
    expected = {"schema_version": 1, "kind": "tinystudio.attempt_outcome", "run_id": identity.run_id,
                "attempt_id": identity.attempt_id, "spec_digest": identity.spec_digest,
                "state": request["state"], "result": result,
                "error": request["error"][:8192] if request["error"] else None}
    if _text(expected) != prepared.outcome_json:
        raise svc.LabError("prepared outcome identity mismatch", 409)
    if not isinstance(verified, dict):
        raise svc.LabError("prepared archive identity mismatch", 409)
    return request, result, verified


def _publish_prepared_finish(db: Session, prepared: FrozenPreparedFinish, *, check_publication=None) -> dict:
    """Recheck fresh authority and atomically associate the verified archive.

    Only bounded local archive bytes are recreated after preparation. This path
    performs no provider reads or writes and never commits its caller's session.
    The retained archive is authoritative; run.result keeps its legacy database
    JSON projection, which need not preserve numeric spellings.
    """
    request, _, verified = _prepared_documents(prepared)
    identity = prepared.identity
    run, attempt = _locked_authority(db, identity)
    replay = _ended_ack(attempt, prepared.payload_digest)
    if replay is not None:
        return replay
    if run.state != "running":
        raise svc.LabError("attempt no longer owns this run", 409)
    if (run.spec_digest != identity.spec_digest or checksum(run.spec) != identity.spec_digest
            or _text(run.spec) != prepared.spec_json):
        raise svc.LabError("immutable specification changed during completion verification", 409)
    state, error = request["state"], request["error"]
    if state == "succeeded" and run.cancel_requested:
        raise svc.LabError("cancellation requested; completion cannot publish success", 409)
    result = svc._canonical_result(db, identity.project_id, identity.attempt_id, request["result"])
    if _text(result) != prepared.canonical_result_json:
        raise svc.LabError("artifact metadata changed during completion verification", 409)
    if check_publication is not None:
        check_publication(db)
    with tempfile.TemporaryDirectory(prefix="ts-lab-outcome-") as root:
        path = Path(root) / "attempt-outcome.json"
        path.write_bytes(prepared.outcome_json.encode())
        archive = svc.store_artifact(db, identity.project_id, path, "attempt-outcome.json", "application/json",
                                     "attempt_outcome", attempt.id, already_stored=True,
                                     visibility="evaluation", verified=verified)
    attempt.state, attempt.result_digest = state, prepared.payload_digest
    run.state, run.result, run.error = state, result, error[:8192] if error else None
    outcome = {"attempt_id": attempt.id, "state": state, "outcome_artifact_id": archive.id, "sha256": archive.sha256}
    if run.error:
        outcome["error"] = run.error
    svc.event(db, run, "server.outcome", outcome)
    svc.event(db, run, state, {"attempt_id": attempt.id, **({"error": run.error} if run.error else {})})
    return {"accepted": True, "state": state}


def _historical_identity(db, target, attempt_id, token):
    """Raw credential read access does not imply a live publication lease."""
    snapshot = _snapshot_target(target)
    attempt = db.scalar(select(LabAttempt).where(LabAttempt.id == attempt_id).execution_options(populate_existing=True))
    token_hash = svc.secret_hash(token)
    if (attempt is None or attempt.target_id != snapshot.id
            or not secrets.compare_digest(attempt.token_hash, token_hash)):
        raise svc.LabError('invalid attempt authority', 403)
    run = db.scalar(select(LabRun).where(LabRun.id == attempt.run_id, LabRun.project_id == snapshot.project_id)
                    .execution_options(populate_existing=True))
    if run is None or run.target_id != snapshot.id:
        raise svc.LabError('invalid attempt authority', 403)
    identity = FinishIdentity(snapshot.project_id, snapshot.id, run.id, attempt.id, token_hash,
                              snapshot.token_hash, run.spec_digest)
    current = db.scalar(select(LabTarget).where(LabTarget.id == snapshot.id).execution_options(populate_existing=True))
    _target_matches(current, identity)
    return identity


def _require_live(run, attempt, identity, state):
    if (run.state != 'running' or attempt.state != 'running'
            or svc.aware(attempt.lease_until) <= svc.now()):
        raise svc.LabError('attempt no longer has a fresh publication lease', 409)
    if run.spec_digest != identity.spec_digest or checksum(run.spec) != identity.spec_digest:
        raise svc.LabError('immutable specification changed during completion verification', 409)
    if state == 'succeeded' and run.cancel_requested:
        raise svc.LabError('cancellation requested; completion cannot publish success', 409)


def _request_key(attempt_id, payload_digest):
    return 'attempt-finalize:'+checksum({'attempt_id': attempt_id, 'payload_digest': payload_digest})


def _artifact_identities(db, identity, result):
    rows = []
    for ref in (result or {}).get('artifacts', []):
        artifact = svc.get_artifact(db, identity.project_id, ref['id'])
        rows.append({'id': artifact.id, 'storage_key': artifact.storage_key,
                     'sha256': artifact.sha256, 'size': artifact.size})
    return rows


def _envelope(row):
    payload = row.payload
    if (row.kind != 'attempt_finalize' or not isinstance(payload, dict)
            or set(payload) != {'envelope_json', 'envelope_sha256'} or checksum(payload) != row.payload_digest):
        raise svc.LabError('invalid attempt finalization envelope', 409)
    text = payload['envelope_json']
    if not isinstance(text, str) or hashlib.sha256(text.encode()).hexdigest() != payload['envelope_sha256']:
        raise svc.LabError('attempt finalization envelope checksum mismatch', 409)
    value = _read_text(text)
    try:
        identity = FinishIdentity(**value['identity'])
        request = _read_text(value['request_json'])
        spec = _read_text(value['spec_json'])
        result = _read_text(value['canonical_result_json'])
        artifacts = _read_text(value['artifact_identities_json'])
        if (set(value) != {'schema_version', 'identity', 'request_json', 'payload_digest', 'spec_json',
                           'canonical_result_json', 'artifact_identities_json'} or value['schema_version'] != 1
                or identity.project_id != row.project_id or checksum(request) != value['payload_digest']
                or checksum(spec) != identity.spec_digest
                or row.idempotency_key != _request_key(identity.attempt_id, value['payload_digest'])
                or set(request) != {'state', 'result', 'error'}
                or request['state'] not in {'succeeded', 'failed', 'cancelled'}
                or result is not None and not isinstance(result, dict) or not isinstance(artifacts, list)):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise svc.LabError('invalid attempt finalization identity', 409) from None
    return value, identity, request, result


def request_finalization(db, target, attempt_id, token, state, result, error):
    """Admit once under fresh authority; identical historical replay is read-only."""
    if state not in {'succeeded', 'failed', 'cancelled'}:
        raise svc.LabError('invalid terminal state')
    identity = _historical_identity(db, target, attempt_id, token)
    request_json = _text({'state': state, 'result': result, 'error': error})
    digest = hashlib.sha256(request_json.encode()).hexdigest()
    run, attempt = _locked_authority(db, identity, historical=True)
    key = _request_key(identity.attempt_id, digest)
    previous = db.scalar(select(LabOperation).where(LabOperation.project_id == identity.project_id,
                                                   LabOperation.idempotency_key == key))
    if previous is not None:
        envelope, prior_identity, _, _ = _envelope(previous)
        if prior_identity != identity or envelope['request_json'] != request_json:
            raise svc.LabError('finalization identity changed since admission', 409)
        return previous
    _require_live(run, attempt, identity, state)
    normalized = svc._canonical_result(db, identity.project_id, identity.attempt_id, result)
    if state == 'succeeded' and (not normalized or not normalized.get('artifacts')):
        raise svc.LabError('success requires a complete evidence manifest')
    envelope = _text({'schema_version': 1, 'identity': asdict(identity), 'request_json': request_json,
                      'payload_digest': digest, 'spec_json': _text(run.spec),
                      'canonical_result_json': _text(normalized),
                      'artifact_identities_json': _text(_artifact_identities(db, identity, normalized))})
    payload = {'envelope_json': envelope, 'envelope_sha256': hashlib.sha256(envelope.encode()).hexdigest()}
    from .operations import _request
    return _request(db, identity.project_id, 'attempt_finalize', payload, key)


def get_finalization(db, target, attempt_id, token, *, operation_id=None, payload_digest=None):
    identity = _historical_identity(db, target, attempt_id, token)
    query = select(LabOperation).where(LabOperation.project_id == identity.project_id, LabOperation.kind == 'attempt_finalize')
    if operation_id is not None:
        query = query.where(LabOperation.id == operation_id)
    elif isinstance(payload_digest, str) and len(payload_digest) == 64 and all(c in '0123456789abcdef' for c in payload_digest):
        query = query.where(LabOperation.idempotency_key == _request_key(identity.attempt_id, payload_digest))
    else:
        raise svc.LabError('exact finalization identity required', 400)
    row = db.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise svc.LabError('finalization not found', 404)
    _, admitted, _, _ = _envelope(row)
    if admitted != identity:
        raise svc.LabError('finalization is outside this exact attempt authority', 403)
    return row


def finalization_dict(row):
    from .operations import operation_dict
    envelope, identity, _, _ = _envelope(row)
    return {**operation_dict(row), 'payload_digest': envelope['payload_digest'], 'attempt_id': identity.attempt_id,
            'run_id': identity.run_id, 'target_id': identity.target_id, 'spec_digest': identity.spec_digest}


def _make_store():
    from .transfer_configuration import TransferPolicy
    from .transfer_storage import MultipartArtifactStore
    return MultipartArtifactStore(svc.storage(), TransferPolicy.from_environment())


def _checkpoint(factory, identity, state, check_authority):
    check_authority()
    with factory() as db:
        target = db.execute(select(LabTarget.project_id, LabTarget.enabled, LabTarget.token_hash)
                            .where(LabTarget.id == identity.target_id)).one_or_none()
        run = db.execute(select(LabRun.project_id, LabRun.target_id, LabRun.state, LabRun.spec_digest, LabRun.cancel_requested)
                         .where(LabRun.id == identity.run_id)).one_or_none()
        attempt = db.execute(select(LabAttempt.target_id, LabAttempt.run_id, LabAttempt.state,
                                    LabAttempt.token_hash, LabAttempt.lease_until)
                             .where(LabAttempt.id == identity.attempt_id)).one_or_none()
        if (target is None or run is None or attempt is None or not target.enabled
                or target.project_id != identity.project_id or run.project_id != identity.project_id
                or target.token_hash != identity.target_token_hash or run.target_id != identity.target_id
                or attempt.target_id != identity.target_id or attempt.run_id != identity.run_id
                or attempt.token_hash != identity.attempt_token_hash or run.spec_digest != identity.spec_digest
                or run.state != 'running' or attempt.state != 'running' or svc.aware(attempt.lease_until) <= svc.now()):
            raise svc.LabError('attempt finalization no longer has fresh matching authority', 409)
        if state == 'succeeded' and run.cancel_requested:
            raise svc.LabError('cancellation requested; completion cannot publish success', 409)


def _check_content_identities(db, envelope, identity, result):
    if _text(_artifact_identities(db, identity, result)) != envelope['artifact_identities_json']:
        raise svc.LabError('artifact content identity changed during completion verification', 409)


def execute_finalization(db, project, payload, directory, *, check_authority, session_factory):
    """Stream stored evidence outside locks; publish only the bounded archive."""
    # Reuse the journal's complete identity validation even when called by a
    # worker carrying only project/payload rather than a route's raw credential.
    shell = LabOperation(project_id=project, kind='attempt_finalize', payload=payload, payload_digest=checksum(payload))
    try:
        raw = _read_text(payload['envelope_json'])
        shell.idempotency_key = _request_key(raw['identity']['attempt_id'], raw['payload_digest'])
        envelope, identity, request, result = _envelope(shell)
    except (KeyError, TypeError, ValueError):
        raise svc.LabError('invalid attempt finalization envelope', 409) from None
    checkpoint = lambda: _checkpoint(session_factory, identity, request['state'], check_authority)
    checkpoint()
    store = _make_store()
    def verify(db, project, normalized, unused_directory):
        if _text(normalized) != envelope['canonical_result_json']:
            raise svc.LabError('artifact metadata changed since finalization admission', 409)
        _check_content_identities(db, envelope, identity, normalized)
        for content in _read_text(envelope['artifact_identities_json']):
            checkpoint()
            store.verify(content['storage_key'], content['size'], content['sha256'], check_authority=checkpoint)
        checkpoint()
    prepared = _prepare_finish(db, identity, envelope['request_json'], verify_result=verify)
    if prepared.spec_json != envelope['spec_json'] or prepared.canonical_result_json != envelope['canonical_result_json']:
        raise svc.LabError('finalization preparation differs from admitted snapshot', 409)
    checkpoint()
    return _publish_prepared_finish(db, prepared,
        check_publication=lambda db: _check_content_identities(db, envelope, identity, result))


def check_finalization_publication_authority(db, row, result):
    """Fresh kind-specific fence AFTER the final operation row lock wait.

    The attempt/run are provisionally terminal in this same transaction, so the
    ordinary live-running checkpoint would reject valid publication. Validate
    that exact provisional state and archive association before committing it.
    """
    envelope, identity, request, normalized = _envelope(row)
    run, attempt = _locked_authority(db, identity, historical=True)
    state = request['state']
    if (svc.aware(attempt.lease_until) <= svc.now() or attempt.state != state or run.state != state
            or attempt.result_digest != envelope['payload_digest']
            or run.spec_digest != identity.spec_digest or checksum(run.spec) != identity.spec_digest
            or _text(run.spec) != envelope['spec_json']
            or _text(result) != _text({'accepted': True, 'state': state})
            or run.error != (request['error'][:8192] if request['error'] else None)):
        raise svc.LabError('provisional finalization no longer has fresh exact authority', 409)
    if state == 'succeeded' and run.cancel_requested:
        raise svc.LabError('cancellation requested; completion cannot publish success', 409)
    final_result = svc._canonical_result(db, identity.project_id, identity.attempt_id, request['result'])
    if _text(final_result) != envelope['canonical_result_json']:
        raise svc.LabError('finalization evidence metadata changed before publication', 409)
    _check_content_identities(db, envelope, identity, normalized)
    if db.get_bind().dialect.name == 'postgresql':
        # Qualify only the legacy JSONB projection in the database's own JSONB
        # equality domain. The retained canonical archive remains exact text.
        from sqlalchemy import Text, cast, literal, or_
        from sqlalchemy.dialects.postgresql import JSONB
        projection = cast(literal(envelope['canonical_result_json'], type_=Text), JSONB)
        same_projection = LabRun.result == projection
        if envelope['canonical_result_json'] == 'null':
            # A legacy SQL NULL also loads as None. Assigning None again may
            # leave that column unchanged rather than writing JSON null.
            same_projection = or_(LabRun.result.is_(None), same_projection)
        matches = db.scalar(select(LabRun.id).where(LabRun.id == identity.run_id, same_projection))
        if matches is None:
            raise svc.LabError('provisional finalization result projection changed', 409)
    elif _text(run.result) != envelope['canonical_result_json']:
        raise svc.LabError('provisional finalization result projection changed', 409)
    document = svc._outcome_document(run, identity.attempt_id, state, normalized, request['error'])
    data = canonical(document)
    sha = hashlib.sha256(data).hexdigest()
    event = db.scalar(select(LabEvent).where(LabEvent.run_id == identity.run_id, LabEvent.kind == 'server.outcome')
                      .order_by(LabEvent.seq.desc()).limit(1))
    archive = db.get(LabArtifact, event.payload.get('outcome_artifact_id')) if event is not None else None
    association = {'attempt_id': identity.attempt_id, 'state': state,
                   'outcome_artifact_id': archive.id if archive else None, 'sha256': sha}
    if run.error:
        association['error'] = run.error
    if (event is None or event.payload != association or archive is None or archive.attempt_id != identity.attempt_id
            or archive.project_id != identity.project_id or archive.sha256 != sha or archive.size != len(data)
            or archive.storage_key != project_key(identity.project_id, 'lab', sha[:2], sha)
            or archive.name != 'attempt-outcome.json' or archive.media_type != 'application/json'
            or archive.role != 'attempt_outcome' or archive.visibility != 'evaluation'):
        raise svc.LabError('provisional finalization archive association changed', 409)
