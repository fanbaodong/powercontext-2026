from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator
from app.config import settings


# ---------- Project ----------

class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("项目名称不能为空白")
        return v.strip()


class ProjectOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    document_count: int = 0
    card_count: int = 0


# ---------- Document ----------

class DocumentImport(BaseModel):
    project_id: int
    title: str = Field(..., min_length=1, max_length=500)
    author: Optional[str] = Field(None, max_length=255)
    source_url: Optional[str] = Field(None, max_length=2000)
    tags: Optional[str] = Field(None, max_length=1000)
    content: str = Field(..., min_length=1)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("标题不能为空白")
        return v.strip()

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        return validate_safe_url(v)

    @field_validator("content")
    @classmethod
    def content_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("内容不能为空")
        if len(v.encode("utf-8")) > settings.max_upload_size_bytes:
            raise ValueError(f"内容大小超过限制 {settings.max_upload_size_bytes} 字节")
        return v


class DocumentOut(BaseModel):
    id: int
    project_id: int
    title: str
    author: Optional[str] = None
    source_url: Optional[str] = None
    tags: Optional[str] = None
    content_hash: str
    char_count: int
    chunk_count: int
    created_at: Optional[datetime] = None
    reading_status: str = "unread"


class DocumentListItem(BaseModel):
    id: int
    title: str
    author: Optional[str] = None
    source_url: Optional[str] = None
    tags: Optional[str] = None
    char_count: int
    chunk_count: int
    created_at: Optional[datetime] = None
    reading_status: str = "unread"
    card_count: int = 0


# ---------- Chunk ----------

class ChunkOut(BaseModel):
    id: int
    document_id: int
    paragraph_number: int
    content: str
    char_count: int


# ---------- Search ----------

class SearchResultItem(BaseModel):
    chunk_id: int
    document_id: int
    paragraph_number: int
    content: str
    relevance: float
    document_title: str
    document_author: Optional[str] = None
    document_source_url: Optional[str] = None
    document_tags: Optional[str] = None


class SearchResponse(BaseModel):
    project_id: int
    query: str
    total: int
    results: list[SearchResultItem]


# ---------- Card ----------

class CardCreate(BaseModel):
    project_id: int
    document_id: int
    chunk_id: int
    annotation: Optional[str] = Field(None, max_length=5000)


class CardUpdate(BaseModel):
    project_id: int
    annotation: Optional[str] = Field(None, max_length=5000)


class CardOut(BaseModel):
    id: int
    project_id: int
    document_id: int
    chunk_id: int
    annotation: Optional[str] = None
    document_title: str = ""
    paragraph_number: int = 0
    chunk_content: str = ""
    created_at: Optional[datetime] = None


# ---------- Progress ----------

class ReadingStatus(str, Enum):
    unread = "unread"
    reading = "reading"
    read = "read"


class ProgressUpdate(BaseModel):
    status: ReadingStatus


class ProgressOut(BaseModel):
    document_id: int
    status: ReadingStatus
    updated_at: Optional[datetime] = None


class ProgressStats(BaseModel):
    total: int
    unread: int
    reading: int
    read: int


# ---------- Helpers ----------

def validate_safe_url(url: str) -> str:
    url = url.strip()
    if not url:
        raise ValueError("URL不能为空")
    if len(url) > 2000:
        raise ValueError("URL长度超限")
    try:
        parsed = urlparse(url)
    except Exception:
        raise ValueError("URL格式无效")
    if parsed.scheme not in ("http", "https"):
        raise ValueError("仅允许 http:// 或 https:// 链接")
    if not parsed.netloc:
        raise ValueError("URL缺少主机名")
    return url
