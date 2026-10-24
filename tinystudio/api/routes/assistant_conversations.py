"""Authenticated assistant conversation persistence, separate from provider execution."""

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from tinystudio.api.deps import current_user, get_db, project_dep
from tinystudio.auth import Principal
from tinystudio.db.models import Project
from tinystudio.services import assistant_conversations as conversations

router = APIRouter(prefix="/api/p/{slug}/assistant", tags=["assistant"])
PRIVATE_CACHE = {"Cache-Control": "private, no-store"}


def require_expected_owner(request: Request, principal: Principal) -> None:
    """Reject a stale browser session identity before accessing private state."""
    expected = request.headers.get("x-conversation-owner")
    if expected is not None and expected != str(principal.user.id):
        raise HTTPException(status_code=403, detail="conversation session changed; reload before continuing", headers={**PRIVATE_CACHE, "X-Conversation-Owner-Mismatch": "true"})


def _etag(project_id: str, user_id: str, revision: int) -> str:
    scope = json.dumps([str(project_id), str(user_id), revision], separators=(",", ":"))
    return '"' + hashlib.sha256(scope.encode("utf-8")).hexdigest() + '"'


def _matches(if_none_match: str, etag: str) -> bool:
    # GET validators use weak comparison, including a list of entity tags.
    return any(value == "*" or value.removeprefix("W/") == etag for part in if_none_match.split(",") if (value := part.strip()))


@router.get("/conversation")
def get_conversation(request: Request, response: Response, project: Project = Depends(project_dep), principal: Principal = Depends(current_user), db: Session = Depends(get_db)) -> Any:
    require_expected_owner(request, principal)
    validator = request.headers.get("if-none-match")
    if validator is not None:
        etag = _etag(project.id, principal.user.id, conversations.revision(db, project.id, principal.user.id))
        if _matches(validator, etag):
            return Response(status_code=304, headers={**PRIVATE_CACHE, "ETag": etag})
    snapshot = conversations.load(db, project.id, principal.user.id)
    # A write can race the cheap check: describe the snapshot actually loaded.
    response.headers.update({**PRIVATE_CACHE, "ETag": _etag(project.id, principal.user.id, snapshot["revision"])})
    return snapshot


@router.put("/conversation")
def put_conversation(body: conversations.ConversationWrite, request: Request, response: Response, project: Project = Depends(project_dep), principal: Principal = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_expected_owner(request, principal)
    try:
        snapshot = conversations.save(db, project.id, principal.user.id, body)
        response.headers.update({**PRIVATE_CACHE, "ETag": _etag(project.id, principal.user.id, snapshot["revision"])})
        return snapshot
    except conversations.ConversationConflict as cause:
        raise HTTPException(status_code=409, detail=str(cause)) from cause
    except conversations.ConversationTooLarge as cause:
        raise HTTPException(status_code=413, detail=str(cause)) from cause
    except ValueError as cause:
        raise HTTPException(status_code=400, detail=str(cause)) from cause
