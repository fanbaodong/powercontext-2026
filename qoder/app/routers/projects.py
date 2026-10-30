from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.models import ProjectCreate, ProjectOut
from app.services import project as project_svc
from app.database import DatabaseError

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("")
def list_projects():
    try:
        return project_svc.list_projects()
    except DatabaseError:
        raise HTTPException(status_code=503, detail="数据库服务暂不可用")


@router.post("", status_code=201)
def create_project(body: ProjectCreate):
    try:
        return project_svc.create_project(body.name, body.description)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except DatabaseError:
        raise HTTPException(status_code=503, detail="数据库服务暂不可用")


@router.get("/{project_id}")
def get_project(project_id: int):
    try:
        row = project_svc.get_project(project_id)
        if row is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        return row
    except HTTPException:
        raise
    except DatabaseError:
        raise HTTPException(status_code=503, detail="数据库服务暂不可用")


@router.delete("/{project_id}")
def delete_project(project_id: int):
    try:
        deleted = project_svc.delete_project(project_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="项目不存在")
        return {"ok": True}
    except HTTPException:
        raise
    except DatabaseError:
        raise HTTPException(status_code=503, detail="数据库服务暂不可用")
