from __future__ import annotations

import hashlib
from typing import Optional

import pymysql

from app.config import settings
from app.database import get_cursor, DatabaseError
from app.models import DocumentImport, validate_safe_url
from app.services.chunking import chunk_content


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validate_extension(filename: Optional[str]) -> None:
    if filename is None:
        return
    lower = filename.lower()
    for ext in settings.allowed_extensions:
        if lower.endswith(ext):
            return
    raise ValueError(f"不支持的文件类型，仅允许: {', '.join(sorted(settings.allowed_extensions))}")


def validate_and_prepare(data: dict) -> DocumentImport:
    """统一校验入口，供 JSON / 上传 / 演示脚本复用。"""
    source_url = data.get("source_url")
    if source_url is not None and source_url != "":
        source_url = validate_safe_url(source_url)
    else:
        source_url = None

    return DocumentImport(
        project_id=data["project_id"],
        title=data["title"],
        author=data.get("author"),
        source_url=source_url,
        tags=data.get("tags"),
        content=data["content"],
    )


def import_document(
    project_id: int,
    title: str,
    content: str,
    author: Optional[str] = None,
    source_url: Optional[str] = None,
    tags: Optional[str] = None,
) -> dict:
    validated = validate_and_prepare({
        "project_id": project_id,
        "title": title,
        "content": content,
        "author": author,
        "source_url": source_url,
        "tags": tags,
    })

    full_hash = _content_hash(validated.content)

    with get_cursor(commit=True) as cur:
        cur.execute("SELECT id FROM projects WHERE id = %s", (validated.project_id,))
        if cur.fetchone() is None:
            raise ValueError(f"项目 {validated.project_id} 不存在")

        cur.execute(
            "SELECT id FROM documents WHERE project_id = %s AND content_hash = %s",
            (validated.project_id, full_hash),
        )
        existing = cur.fetchone()
        if existing:
            cur.execute("SELECT * FROM documents WHERE id = %s", (existing["id"],))
            return cur.fetchone()

        try:
            cur.execute(
                """INSERT INTO documents
                   (project_id, title, author, source_url, tags, content_hash, char_count, chunk_count)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 0)""",
                (validated.project_id, validated.title, validated.author,
                 validated.source_url, validated.tags, full_hash, len(validated.content)),
            )
            doc_id = cur.lastrowid

            chunks = chunk_content(validated.content)
            for chunk in chunks:
                chunk_hash = _content_hash(chunk["text"])
                cur.execute(
                    """INSERT INTO chunks
                       (document_id, paragraph_number, content, content_hash, char_count)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (doc_id, chunk["paragraph_number"], chunk["text"], chunk_hash, len(chunk["text"])),
                )

            cur.execute(
                "UPDATE documents SET chunk_count = %s WHERE id = %s",
                (len(chunks), doc_id),
            )

            cur.execute(
                "INSERT INTO reading_progress (document_id, status) VALUES (%s, 'unread')",
                (doc_id,),
            )

            cur.execute("SELECT * FROM documents WHERE id = %s", (doc_id,))
            return cur.fetchone()

        except pymysql.err.IntegrityError:
            cur.execute(
                "SELECT id FROM documents WHERE project_id = %s AND content_hash = %s",
                (validated.project_id, full_hash),
            )
            existing = cur.fetchone()
            if existing:
                cur.execute("SELECT * FROM documents WHERE id = %s", (existing["id"],))
                return cur.fetchone()
            raise


def list_documents(project_id: int) -> list[dict]:
    with get_cursor() as cur:
        cur.execute("""
            SELECT d.*,
                   COALESCE(rp.status, 'unread') AS reading_status,
                   (SELECT COUNT(*) FROM cards c WHERE c.document_id = d.id) AS card_count
            FROM documents d
            LEFT JOIN reading_progress rp ON rp.document_id = d.id
            WHERE d.project_id = %s
            ORDER BY d.id DESC
        """, (project_id,))
        return cur.fetchall()


def get_document(doc_id: int) -> Optional[dict]:
    with get_cursor() as cur:
        cur.execute("""
            SELECT d.*,
                   COALESCE(rp.status, 'unread') AS reading_status
            FROM documents d
            LEFT JOIN reading_progress rp ON rp.document_id = d.id
            WHERE d.id = %s
        """, (doc_id,))
        return cur.fetchone()


def get_document_chunks(doc_id: int) -> list[dict]:
    with get_cursor() as cur:
        cur.execute(
            "SELECT * FROM chunks WHERE document_id = %s ORDER BY paragraph_number ASC",
            (doc_id,),
        )
        return cur.fetchall()


def delete_document(doc_id: int) -> bool:
    with get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
        return cur.rowcount > 0
