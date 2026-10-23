"""Authenticated assistant conversation persistence, separate from provider execution."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from tinystudio.api.deps import current_user, get_db, project_dep
from tinystudio.auth import Principal
from tinystudio.db.models import Project
from tinystudio.services import assistant_conversations as conversations

router = APIRouter(prefix="/api/p/{slug}/assistant", tags=["assistant"])


@router.get("/conversation")
def get_conversation(project: Project = Depends(project_dep), principal: Principal = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return conversations.load(db, project.id, principal.user.id)


@router.put("/conversation")
def put_conversation(body: conversations.ConversationWrite, project: Project = Depends(project_dep), principal: Principal = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return conversations.save(db, project.id, principal.user.id, body)
    except conversations.ConversationConflict as cause:
        raise HTTPException(status_code=409, detail=str(cause)) from cause
    except conversations.ConversationTooLarge as cause:
        raise HTTPException(status_code=413, detail=str(cause)) from cause
    except ValueError as cause:
        raise HTTPException(status_code=400, detail=str(cause)) from cause
