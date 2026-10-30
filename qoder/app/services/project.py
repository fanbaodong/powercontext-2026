from __future__ import annotations

from typing import Optional

from app.database import get_cursor, DatabaseError


def create_project(name: str, description: Optional[str] = None) -> dict:
    if not name or not name.strip():
        raise ValueError("项目名称不能为空白")

    with get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO projects (name, description) VALUES (%s, %s)",
            (name.strip(), description),
        )
        project_id = cur.lastrowid
        cur.execute("SELECT * FROM projects WHERE id = %s", (project_id,))
        return cur.fetchone()


def list_projects() -> list[dict]:
    with get_cursor() as cur:
        cur.execute("""
            SELECT p.*,
                   (SELECT COUNT(*) FROM documents d WHERE d.project_id = p.id) AS document_count,
                   (SELECT COUNT(*) FROM cards c WHERE c.project_id = p.id) AS card_count
            FROM projects p
            ORDER BY p.id ASC
        """)
        return cur.fetchall()


def get_project(project_id: int) -> Optional[dict]:
    with get_cursor() as cur:
        cur.execute("""
            SELECT p.*,
                   (SELECT COUNT(*) FROM documents d WHERE d.project_id = p.id) AS document_count,
                   (SELECT COUNT(*) FROM cards c WHERE c.project_id = p.id) AS card_count
            FROM projects p WHERE p.id = %s
        """, (project_id,))
        return cur.fetchone()


def delete_project(project_id: int) -> bool:
    with get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM projects WHERE id = %s", (project_id,))
        return cur.rowcount > 0
