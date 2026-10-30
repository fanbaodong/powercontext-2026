from __future__ import annotations

from typing import Optional

from app.database import get_cursor, DatabaseError


def get_progress(document_id: int) -> dict:
    with get_cursor() as cur:
        cur.execute(
            "SELECT * FROM reading_progress WHERE document_id = %s",
            (document_id,),
        )
        row = cur.fetchone()
        if row is None:
            return {"document_id": document_id, "status": "unread", "updated_at": None}
        return row


def update_progress(document_id: int, status: str) -> dict:
    if status not in ("unread", "reading", "read"):
        raise ValueError(f"无效状态: {status}")

    with get_cursor(commit=True) as cur:
        cur.execute(
            "SELECT id FROM reading_progress WHERE document_id = %s",
            (document_id,),
        )
        existing = cur.fetchone()
        if existing:
            cur.execute(
                "UPDATE reading_progress SET status = %s WHERE document_id = %s",
                (status, document_id),
            )
        else:
            cur.execute(
                "INSERT INTO reading_progress (document_id, status) VALUES (%s, %s)",
                (document_id, status),
            )

    return get_progress(document_id)


def get_project_stats(project_id: int) -> dict:
    with get_cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN rp.status = 'unread' THEN 1 ELSE 0 END) AS unread_count,
                SUM(CASE WHEN rp.status = 'reading' THEN 1 ELSE 0 END) AS reading_count,
                SUM(CASE WHEN rp.status = 'read' THEN 1 ELSE 0 END) AS read_count
            FROM documents d
            LEFT JOIN reading_progress rp ON rp.document_id = d.id
            WHERE d.project_id = %s
        """, (project_id,))
        row = cur.fetchone()
        return {
            "total": row.get("total", 0) or 0,
            "unread": row.get("unread_count", 0) or 0,
            "reading": row.get("reading_count", 0) or 0,
            "read": row.get("read_count", 0) or 0,
        }
