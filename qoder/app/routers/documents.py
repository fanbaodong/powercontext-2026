from __future__ import annotations

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query
from typing import Optional

from app.config import settings
from app.models import DocumentImport
from app.services import document as document_svc
from app.database import DatabaseError

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.get("/project/{project_id}")
def list_documents(project_id: int):
    try:
        return document_svc.list_documents(project_id)
    except DatabaseError as exc:
        raise HTTPException(status_code=503, detail="数据库服务暂不可用")


@router.get("/{doc_id}")
def get_document(doc_id: int):
    try:
        row = document_svc.get_document(doc_id)
        if row is None:
            raise HTTPException(status_code=404, detail="文档不存在")
        return row
    except HTTPException:
        raise
    except DatabaseError:
        raise HTTPException(status_code=503, detail="数据库服务暂不可用")


@router.get("/{doc_id}/chunks")
def get_document_chunks(doc_id: int):
    try:
        return document_svc.get_document_chunks(doc_id)
    except DatabaseError:
        raise HTTPException(status_code=503, detail="数据库服务暂不可用")


@router.post("", status_code=201)
def import_document(body: DocumentImport):
    try:
        return document_svc.import_document(
            project_id=body.project_id,
            title=body.title,
            content=body.content,
            author=body.author,
            source_url=body.source_url,
            tags=body.tags,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except DatabaseError:
        raise HTTPException(status_code=503, detail="数据库服务暂不可用")


@router.post("/upload", status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    project_id: int = Form(...),
    title: str = Form(...),
    author: Optional[str] = Form(None),
    source_url: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
):
    document_svc._validate_extension(file.filename)

    limit = settings.max_upload_size_bytes
    raw = await file.read(limit + 1)
    if len(raw) > limit:
        raise HTTPException(status_code=400, detail=f"文件大小超过限制 {limit} 字节")

    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="文件编码必须为 UTF-8")

    if not content.strip():
        raise HTTPException(status_code=400, detail="文件内容为空")

    if not title or not title.strip():
        raise HTTPException(status_code=400, detail="标题不能为空白")

    try:
        return document_svc.import_document(
            project_id=project_id,
            title=title,
            content=content,
            author=author,
            source_url=source_url,
            tags=tags,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except DatabaseError:
        raise HTTPException(status_code=503, detail="数据库服务暂不可用")


@router.delete("/{doc_id}")
def delete_document(doc_id: int):
    try:
        deleted = document_svc.delete_document(doc_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="文档不存在")
        return {"ok": True}
    except HTTPException:
        raise
    except DatabaseError:
        raise HTTPException(status_code=503, detail="数据库服务暂不可用")
