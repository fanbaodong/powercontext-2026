from __future__ import annotations

import re
from typing import Optional

from app.database import get_cursor, DatabaseError


def _validate_ownership(project_id: int, document_id: int, chunk_id: int) -> None:
    """校验 project → document → chunk 归属链。不匹配则抛出 ValueError。"""
    with get_cursor() as cur:
        cur.execute(
            "SELECT id, project_id FROM documents WHERE id = %s",
            (document_id,),
        )
        doc = cur.fetchone()
        if doc is None:
            raise ValueError("文档不存在")
        if doc["project_id"] != project_id:
            raise ValueError("文档不属于指定项目")

        cur.execute(
            "SELECT id, document_id FROM chunks WHERE id = %s",
            (chunk_id,),
        )
        chunk = cur.fetchone()
        if chunk is None:
            raise ValueError("段落不存在")
        if chunk["document_id"] != document_id:
            raise ValueError("段落不属于指定文档")


def create_card(
    project_id: int,
    document_id: int,
    chunk_id: int,
    annotation: Optional[str] = None,
) -> dict:
    _validate_ownership(project_id, document_id, chunk_id)

    with get_cursor(commit=True) as cur:
        cur.execute(
            """INSERT INTO cards (project_id, document_id, chunk_id, annotation)
               VALUES (%s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE annotation = VALUES(annotation)""",
            (project_id, document_id, chunk_id, annotation),
        )
        cur.execute(
            "SELECT id FROM cards WHERE project_id = %s AND document_id = %s AND chunk_id = %s",
            (project_id, document_id, chunk_id),
        )
        row = cur.fetchone()
        card_id = row["id"]

    return get_card(card_id)


def get_card(card_id: int) -> Optional[dict]:
    with get_cursor() as cur:
        cur.execute("""
            SELECT c.id, c.project_id, c.document_id, c.chunk_id,
                   c.annotation, c.created_at,
                   d.title AS document_title,
                   d.author AS document_author,
                   d.source_url AS document_source_url,
                   ch.paragraph_number,
                   ch.content AS chunk_content
            FROM cards c
            JOIN documents d ON c.document_id = d.id
            JOIN chunks ch ON c.chunk_id = ch.id
            WHERE c.id = %s
        """, (card_id,))
        return cur.fetchone()


def list_cards(project_id: int) -> list[dict]:
    with get_cursor() as cur:
        cur.execute("""
            SELECT c.id, c.project_id, c.document_id, c.chunk_id,
                   c.annotation, c.created_at,
                   d.title AS document_title,
                   d.author AS document_author,
                   d.source_url AS document_source_url,
                   ch.paragraph_number,
                   ch.content AS chunk_content
            FROM cards c
            JOIN documents d ON c.document_id = d.id
            JOIN chunks ch ON c.chunk_id = ch.id
            WHERE c.project_id = %s
            ORDER BY c.id DESC
        """, (project_id,))
        return cur.fetchall()


def delete_card(card_id: int, project_id: int) -> bool:
    with get_cursor(commit=True) as cur:
        cur.execute(
            "DELETE FROM cards WHERE id = %s AND project_id = %s",
            (card_id, project_id),
        )
        return cur.rowcount > 0


def update_card_annotation(card_id: int, project_id: int, annotation: Optional[str]) -> Optional[dict]:
    if annotation is not None and len(annotation) > 5000:
        raise ValueError("批注长度不能超过5000字符")

    with get_cursor(commit=True) as cur:
        cur.execute(
            "SELECT id FROM cards WHERE id = %s AND project_id = %s",
            (card_id, project_id),
        )
        if cur.fetchone() is None:
            return None
        cur.execute(
            "UPDATE cards SET annotation = %s WHERE id = %s AND project_id = %s",
            (annotation, card_id, project_id),
        )
    return get_card(card_id)


def is_card_saved(project_id: int, document_id: int, chunk_id: int) -> bool:
    with get_cursor() as cur:
        cur.execute(
            "SELECT 1 FROM cards WHERE project_id = %s AND document_id = %s AND chunk_id = %s",
            (project_id, document_id, chunk_id),
        )
        return cur.fetchone() is not None


def _escape_md_text(text: str) -> str:
    """转义用户文本，使其在 Markdown 中安全渲染为纯文本，保留原始字符。"""
    if not text:
        return ""
    result = text.replace("\\", "\\\\")
    result = result.replace("<", "&lt;")
    result = result.replace(">", "&gt;")
    result = re.sub(
        r"\[([^\]]*)\]\(\s*javascript:[^)]*\)",
        r"\1",
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(r"(?<!\\)([`\[\]*_~|])", r"\\\1", result)
    return result


def export_cards_markdown(project_id: int, project_name: str) -> str:
    cards = list_cards(project_id)
    safe_name = _escape_md_text(project_name)
    lines = [f"# {safe_name} - 证据卡片导出", ""]

    if not cards:
        lines.append("暂无收藏的证据卡片。")
        return "\n".join(lines)

    for idx, card in enumerate(cards, 1):
        lines.append(f"## 卡片 {idx}")
        lines.append("")
        doc_title = _escape_md_text(card.get("document_title", "未知"))
        lines.append(f"- **来源文档**: {doc_title}")
        author = card.get("document_author")
        if author:
            lines.append(f"- **作者**: {_escape_md_text(author)}")
        source_url = card.get("document_source_url")
        if source_url:
            lines.append(f"- **来源URL**: {_escape_md_text(source_url)}")
        lines.append(f"- **段落编号**: 第 {card.get('paragraph_number', '?')} 段")
        doc_id = card.get("document_id", "")
        chunk_id = card.get("chunk_id", "")
        lines.append(f"- **定位**: document_id={doc_id}, chunk_id={chunk_id}")
        lines.append("")
        lines.append("### 原文摘录")
        lines.append("")
        chunk_text = _escape_md_text(card.get("chunk_content", ""))
        for line in chunk_text.split("\n"):
            lines.append(f"> {line}")
        lines.append("")
        if card.get("annotation"):
            lines.append("### 批注")
            lines.append("")
            lines.append(_escape_md_text(card["annotation"]))
            lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)
