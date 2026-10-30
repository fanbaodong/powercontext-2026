from __future__ import annotations

import re

from app.database import get_cursor, DatabaseError


def search_documents(project_id: int, query: str, limit: int = 50) -> list[dict]:
    """使用 seekdb FULLTEXT MATCH...AGAINST(NATURAL LANGUAGE MODE) 进行关键字检索。

    仅搜索本项目内的文档分块，返回相关性得分。
    空查询返回空列表。
    """
    if not query or not query.strip():
        return []

    safe_query = _sanitize_match_query(query.strip())
    if not safe_query:
        return []

    with get_cursor() as cur:
        sql = """
            SELECT
                c.id AS chunk_id,
                c.document_id,
                c.paragraph_number,
                c.content,
                c.char_count,
                d.title AS document_title,
                d.author AS document_author,
                d.source_url AS document_source_url,
                d.tags AS document_tags,
                MATCH(c.content) AGAINST(%s IN NATURAL LANGUAGE MODE) AS relevance
            FROM chunks c
            JOIN documents d ON c.document_id = d.id
            WHERE d.project_id = %s
              AND MATCH(c.content) AGAINST(%s IN NATURAL LANGUAGE MODE)
            ORDER BY relevance DESC
            LIMIT %s
        """
        cur.execute(sql, (safe_query, project_id, safe_query, limit))
        rows = cur.fetchall()

    return rows


def _sanitize_match_query(query: str) -> str:
    """保留中文、字母、数字和空格，移除 FULLTEXT 特殊字符。"""
    cleaned = re.sub(r"[^\u4e00-\u9fff\u0030-\u0039\u0041-\u005a\u0061-\u007a\s]", " ", query)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned
