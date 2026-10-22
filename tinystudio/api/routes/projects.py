"""Projects: the containers everything else lives in."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from tinystudio.api.deps import current_user, get_db, project_dep, require_admin, wrap
from tinystudio.auth import Principal
from tinystudio.db.models import Project
from tinystudio.services import projects as projects_svc

router = APIRouter(prefix="/api", tags=["projects"])


class ProjectBody(BaseModel):
    name: str
    goal: str = ""


class ProjectPatchBody(BaseModel):
    name: str | None = None
    goal: str | None = None


@router.get("/projects")
def list_projects(db: Session = Depends(get_db), _: Principal = Depends(current_user)) -> list[dict[str, Any]]:
    return projects_svc.list_projects(db)


@router.post("/projects")
def create_project(body: ProjectBody, db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> dict[str, Any]:
    return projects_svc.project_dict(wrap(lambda: projects_svc.create_project(db, body.name, body.goal, principal.user)))


@router.get("/p/{slug}")
def get_project(project: Project = Depends(project_dep)) -> dict[str, Any]:
    return projects_svc.project_dict(project)


@router.patch("/p/{slug}")
def patch_project(body: ProjectPatchBody, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    return projects_svc.project_dict(wrap(lambda: projects_svc.update_project(db, project, body.name, body.goal)))


@router.delete("/p/{slug}")
def delete_project(project: Project = Depends(project_dep), db: Session = Depends(get_db), _: Principal = Depends(require_admin)) -> dict[str, Any]:
    slug = project.slug
    wrap(lambda: projects_svc.delete_project(db, project))
    return {"deleted": slug}


@router.get("/p/{slug}/overview")
def overview(project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    return projects_svc.overview(db, project)
