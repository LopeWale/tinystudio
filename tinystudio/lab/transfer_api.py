"""Small authenticated transfer controls; object bodies bypass the Studio API."""
from typing import Annotated
import secrets

from fastapi import Depends, Header, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy.orm import Session
from sqlalchemy import select

from tinystudio.api.deps import get_db, project_dep
from tinystudio.db.models import Project
from .models import LabAttempt, LabTarget, LabTransfer
from . import service as svc
from .client_paths import RunnerApiPaths
from .finalization_configuration import FINALIZATION_PROTOCOL_VERSION
from .transfer_configuration import AttemptArtifactApiPaths, TransferApiPaths, TransferPolicy
from .transfer_storage import MultipartArtifactStore, TransferStorageError


DB = Annotated[Session, Depends(get_db)]
ProjectDep = Annotated[Project, Depends(project_dep)]


def _target_auth(target_id: str, db: DB, authorization: str = Header(default='')):
    # Reuse the established credential contract without an import cycle during
    # router registration or when this module is imported on its own.
    from .api import target_auth
    return target_auth(target_id, db, authorization)


TargetDep = Annotated[LabTarget, Depends(_target_auth)]


class TransferRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str
    size: Annotated[StrictInt, Field(ge=0)]
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    media_type: str = 'application/octet-stream'
    role: str | None = None
    visibility: str = Field(default='model', pattern=r'^(model|evaluation|truth)$')
    idempotency_key: str = Field(min_length=1)

    def intent(self):
        return self.model_dump(exclude={'idempotency_key'}, exclude_none=True)


def _store():
    return MultipartArtifactStore(svc.storage(), TransferPolicy.from_environment())


def _require_remote():
    if not _store().storage.remote:
        raise svc.LabError('resumable object transfers require R2; local storage uses the regular inputs upload', 409)


def _result(row):
    from .transfer_service import transfer_dict
    return transfer_dict(row)


def _project_row(db, project, session_id, *, inputs_only=True):
    from .transfer_service import get_transfer, require_scope
    row = get_transfer(db, project.id, session_id)
    if inputs_only:
        require_scope(row)
    return row


def _runner_row(db, target, attempt_id, session_id, lease, *, read_only=False):
    from .transfer_service import attempt_identity, get_transfer, require_scope
    if read_only:
        # Status access proves the exact historical credential, without
        # treating an expired execution lease as publication authority.
        attempt = db.get(LabAttempt, attempt_id)
        if (attempt is None or attempt.target_id != target.id
                or not secrets.compare_digest(attempt.token_hash, svc.secret_hash(lease))):
            raise svc.LabError('invalid attempt authority', 403)
        run = svc.get_run(db, target.project_id, attempt.run_id)
    else:
        run, attempt = svc.attempt_authority(db, target, attempt_id, lease)
    row = get_transfer(db, target.project_id, session_id)
    authority = ({'run_id': run.id, 'attempt_id': attempt.id, 'target_id': target.id,
                  'token_hash': attempt.token_hash, 'target_token_hash': target.token_hash, 'spec_digest': run.spec_digest}
                 if read_only else attempt_identity(run, attempt, target))
    require_scope(row, authority)
    # Never keep target/run authority locks across provider requests.
    db.commit()
    return row


def _control(action, db, project_id, session_id, *args, **options):
    from . import transfer_service as transfers
    try:
        result = getattr(transfers, action)(db, project_id, session_id, *args, store=_store(), **options)
        db.commit()  # A returned identity is already independently visible.
        return result
    except TransferStorageError:
        raise svc.LabError('artifact transfer control is unconfirmed; inspect its durable session before retrying', 503) from None


def install(project_routes, runner_routes):
    """Share route definitions and object controls across project/target scopes."""
    project_paths = TransferApiPaths.at_lab('')
    attempt_paths = AttemptArtifactApiPaths.at_runner(RunnerApiPaths().attempt_route)
    runner_paths = attempt_paths.transfers

    @project_routes.get(project_paths.policy_route)
    def policy(db: DB, project: ProjectDep):
        store = _store()
        return {'backend': 'r2' if store.storage.remote else 'local',
                'resumable': store.storage.remote, 'policy': TransferPolicy.from_environment().as_dict()}

    @runner_routes.get(attempt_paths.policy_route)
    def runner_policy(id_: str, db: DB, target: TargetDep, x_lab_lease: str = Header(default='')):
        # Select the protocol before artifact mutation; a target credential
        # alone does not confer authority over another attempt's execution.
        svc.attempt_authority(db, target, id_, x_lab_lease)
        db.commit()
        store = _store()
        return {'protocol_version': 1, 'backend': 'r2' if store.storage.remote else 'local',
                'resumable': store.storage.remote, 'direct_download': store.storage.remote,
                'finalization_protocol_version': FINALIZATION_PROTOCOL_VERSION,
                'policy': TransferPolicy.from_environment().as_dict()}

    @project_routes.get(project_paths.collection)
    def transfers(db: DB, project: ProjectDep, after: str | None = None,
                  limit: int | None = Query(default=None, ge=0)):
        query = select(LabTransfer).where(LabTransfer.project_id == project.id).order_by(LabTransfer.id)
        if after is not None:
            query = query.where(LabTransfer.id > after)
        if limit is not None:
            query = query.limit(limit)
        return [_result(row) for row in db.scalars(query)]

    @project_routes.post(project_paths.collection, status_code=201)
    def start_transfer(body: TransferRequest, db: DB, project: ProjectDep):
        from .transfer_service import admit
        _require_remote()
        row = admit(db, project.id, body.intent(), body.idempotency_key, policy=TransferPolicy.from_environment())
        id_ = row.id
        db.commit()
        return _result(_control('initialize', db, project.id, id_))

    @project_routes.get(project_paths.session_route)
    def transfer(session_id: str, db: DB, project: ProjectDep):
        return _result(_project_row(db, project, session_id, inputs_only=False))

    @project_routes.post(project_paths.initialize_route)
    def initialize(session_id: str, db: DB, project: ProjectDep):
        _project_row(db, project, session_id)
        db.commit()
        return _result(_control('initialize', db, project.id, session_id))

    @project_routes.get(project_paths.parts_route)
    def inspect(session_id: str, db: DB, project: ProjectDep):
        _project_row(db, project, session_id)
        db.commit()
        return _control('inspect', db, project.id, session_id)

    @project_routes.post(project_paths.part_route)
    def part(session_id: str, part_number: int, db: DB, project: ProjectDep):
        _project_row(db, project, session_id)
        db.commit()
        return _control('sign_part', db, project.id, session_id, part_number)

    @project_routes.post(project_paths.complete_route, status_code=202)
    def complete(session_id: str, db: DB, project: ProjectDep):
        _project_row(db, project, session_id)
        db.commit()
        return _result(_control('complete', db, project.id, session_id))

    @project_routes.post(project_paths.abort_route)
    def abort(session_id: str, db: DB, project: ProjectDep):
        _project_row(db, project, session_id, inputs_only=False)
        db.commit()
        return _result(_control('abort', db, project.id, session_id, project_authority=True))

    @runner_routes.post(runner_paths.collection, status_code=201)
    def start_output(id_: str, body: TransferRequest, db: DB, target: TargetDep, x_lab_lease: str = Header(default='')):
        from .transfer_service import admit, attempt_identity
        _require_remote()
        run, attempt = svc.attempt_authority(db, target, id_, x_lab_lease)
        authority = attempt_identity(run, attempt, target)
        project_id = target.project_id
        # Preserve the authenticated hashes before releasing these locks. The
        # admission transaction then acquires project-first locks and rechecks
        # them; project deletion must never wait on a run while we wait on it.
        db.commit()
        row = admit(db, project_id, body.intent(), body.idempotency_key,
                    attempt_authority=authority, policy=TransferPolicy.from_environment())
        session_id = row.id
        db.commit()
        return _result(_control('initialize', db, project_id, session_id))

    @runner_routes.get(runner_paths.session_route)
    def output(id_: str, session_id: str, db: DB, target: TargetDep, x_lab_lease: str = Header(default='')):
        return _result(_runner_row(db, target, id_, session_id, x_lab_lease, read_only=True))

    @runner_routes.post(runner_paths.initialize_route)
    def initialize_output(id_: str, session_id: str, db: DB, target: TargetDep, x_lab_lease: str = Header(default='')):
        _runner_row(db, target, id_, session_id, x_lab_lease)
        return _result(_control('initialize', db, target.project_id, session_id))

    @runner_routes.get(runner_paths.parts_route)
    def inspect_output(id_: str, session_id: str, db: DB, target: TargetDep, x_lab_lease: str = Header(default='')):
        _runner_row(db, target, id_, session_id, x_lab_lease)
        return _control('inspect', db, target.project_id, session_id)

    @runner_routes.post(runner_paths.part_route)
    def output_part(id_: str, session_id: str, part_number: int, db: DB, target: TargetDep, x_lab_lease: str = Header(default='')):
        _runner_row(db, target, id_, session_id, x_lab_lease)
        return _control('sign_part', db, target.project_id, session_id, part_number)

    @runner_routes.post(runner_paths.complete_route, status_code=202)
    def complete_output(id_: str, session_id: str, db: DB, target: TargetDep, x_lab_lease: str = Header(default='')):
        _runner_row(db, target, id_, session_id, x_lab_lease)
        return _result(_control('complete', db, target.project_id, session_id))

    @runner_routes.post(runner_paths.abort_route)
    def abort_output(id_: str, session_id: str, db: DB, target: TargetDep, x_lab_lease: str = Header(default='')):
        _runner_row(db, target, id_, session_id, x_lab_lease)
        return _result(_control('abort', db, target.project_id, session_id))

    @project_routes.post('/artifacts/{id_}/download')
    def download(id_: str, db: DB, project: ProjectDep):
        row = svc.get_artifact(db, project.id, id_)
        store = _store()
        if not store.storage.remote:
            return {'mode': 'proxy', 'artifact': svc.artifact_dict(row)}
        return {'mode': 'direct', 'artifact': svc.artifact_dict(row), **store.sign_download(row.storage_key)}

    @runner_routes.post(attempt_paths.input_download_route)
    def download_input(id_: str, artifact_id: str, db: DB, target: TargetDep, x_lab_lease: str = Header(default='')):
        run, _ = svc.attempt_authority(db, target, id_, x_lab_lease)
        if artifact_id not in {item['artifact_id'] for item in run.spec['definition']['inputs']}:
            raise svc.LabError("artifact is outside this attempt's input manifest", 403)
        row = svc.get_artifact(db, target.project_id, artifact_id)
        store = _store()
        db.commit()
        if not store.storage.remote:
            return {'mode': 'proxy', 'artifact': svc.artifact_dict(row)}
        return {'mode': 'direct', 'artifact': svc.artifact_dict(row), **store.sign_download(row.storage_key)}
