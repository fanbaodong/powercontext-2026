from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models import ProgressUpdate, ProgressOut
from app.services import progress as progress_svc
from app.database import DatabaseError

router = APIRouter(prefix="/api/progress", tags=["progress"])


@router.get("/document/{document_id}")
def get_progress(document_id: int):
    try:
        return progress_svc.get_progress(document_id)
    except DatabaseError:
        raise HTTPException(status_code=503, detail="数据库服务暂不可用")


@router.put("/document/{document_id}")
def update_progress(document_id: int, body: ProgressUpdate):
    try:
        return progress_svc.update_progress(document_id, body.status.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except DatabaseError:
        raise HTTPException(status_code=503, detail="数据库服务暂不可用")


@router.get("/project/{project_id}/stats")
def get_project_stats(project_id: int):
    try:
        return progress_svc.get_project_stats(project_id)
    except DatabaseError:
        raise HTTPException(status_code=503, detail="数据库服务暂不可用")
