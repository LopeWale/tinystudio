"""Authenticated, user/project scoped attachment endpoints."""
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from tinystudio.api.deps import current_user, get_db, project_dep
from tinystudio.api.routes.assistant_conversations import require_expected_owner
from tinystudio.auth import Principal
from tinystudio.db.models import Project
from tinystudio.services import assistant_attachments as attachments
from tinystudio.storage import storage

router = APIRouter(prefix='/api/p/{slug}/assistant/attachments', tags=['assistant'])


@router.get('/config')
def config(principal: Principal = Depends(current_user), project: Project = Depends(project_dep)):
    return attachments.configuration()


@router.post('')
async def upload(request: Request, file: UploadFile = File(...), project: Project = Depends(project_dep), principal: Principal = Depends(current_user), db: Session = Depends(get_db)):
    require_expected_owner(request, principal)
    data = await file.read(attachments.MAX_BYTES + 1)
    try:
        return await run_in_threadpool(attachments.create, db, project.id, principal.user.id, file.filename or '', data)
    except attachments.ExtractionBusy as cause:
        raise HTTPException(503, str(cause)) from cause
    except (ValueError, RuntimeError) as cause:
        raise HTTPException(400, str(cause)) from cause
    finally:
        await file.close()


@router.get('/{attachment_id}')
def detail(attachment_id: str, request: Request, project: Project = Depends(project_dep), principal: Principal = Depends(current_user), db: Session = Depends(get_db)):
    require_expected_owner(request, principal)
    try:
        return attachments.metadata(attachments.owned(db, project.id, principal.user.id, attachment_id))
    except ValueError as cause:
        raise HTTPException(404, str(cause)) from cause


@router.delete('/{attachment_id}')
def delete(attachment_id: str, request: Request, project: Project = Depends(project_dep), principal: Principal = Depends(current_user), db: Session = Depends(get_db)):
    require_expected_owner(request, principal)
    try:
        row = attachments.owned(db, project.id, principal.user.id, attachment_id)
    except ValueError as cause:
        raise HTTPException(404, str(cause)) from cause
    storage().delete(row.storage_key)
    db.delete(row)
    db.flush()
    return {'deleted': attachment_id}
