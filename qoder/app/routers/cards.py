from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from app.models import CardCreate, CardUpdate
from app.services import card as card_svc
from app.database import DatabaseError

router = APIRouter(prefix="/api/cards", tags=["cards"])


@router.get("/project/{project_id}")
def list_cards(project_id: int):
    try:
        return card_svc.list_cards(project_id)
    except DatabaseError:
        raise HTTPException(status_code=503, detail="数据库服务暂不可用")


@router.post("", status_code=201)
def create_card(body: CardCreate):
    try:
        return card_svc.create_card(
            project_id=body.project_id,
            document_id=body.document_id,
            chunk_id=body.chunk_id,
            annotation=body.annotation,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except DatabaseError:
        raise HTTPException(status_code=503, detail="数据库服务暂不可用")


@router.get("/check")
def check_card(project_id: int, document_id: int, chunk_id: int):
    try:
        saved = card_svc.is_card_saved(project_id, document_id, chunk_id)
        return {"saved": saved}
    except DatabaseError:
        raise HTTPException(status_code=503, detail="数据库服务暂不可用")


@router.put("/{card_id}")
def update_card(card_id: int, body: CardUpdate):
    try:
        result = card_svc.update_card_annotation(card_id, body.project_id, body.annotation)
        if result is None:
            raise HTTPException(status_code=404, detail="卡片不存在")
        return result
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except DatabaseError:
        raise HTTPException(status_code=503, detail="数据库服务暂不可用")


@router.delete("/{card_id}")
def delete_card(card_id: int, project_id: int):
    try:
        deleted = card_svc.delete_card(card_id, project_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="卡片不存在")
        return {"ok": True}
    except HTTPException:
        raise
    except DatabaseError:
        raise HTTPException(status_code=503, detail="数据库服务暂不可用")


@router.get("/project/{project_id}/export")
def export_markdown(project_id: int, project_name: str = Query("研究项目")):
    try:
        md = card_svc.export_cards_markdown(project_id, project_name)
        return {"markdown": md}
    except DatabaseError:
        raise HTTPException(status_code=503, detail="数据库服务暂不可用")
