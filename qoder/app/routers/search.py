from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.services import search as search_svc
from app.database import DatabaseError

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("/project/{project_id}")
def search_in_project(project_id: int, q: str = Query(..., min_length=0), limit: int = Query(50, ge=1, le=200)):
    try:
        results = search_svc.search_documents(project_id, q, limit)
        return {
            "project_id": project_id,
            "query": q,
            "total": len(results),
            "results": results,
        }
    except DatabaseError:
        raise HTTPException(status_code=503, detail="数据库服务暂不可用")
