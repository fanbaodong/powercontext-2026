from __future__ import annotations

import re

from app.config import settings


def chunk_content(text: str) -> list[dict]:
    """将文本按段落切块，返回 [{"paragraph_number": int, "text": str}, ...]。

    规则:
    - 统一 CRLF/CR 为 LF
    - 按连续空行分割段落
    - 保留所有非空段落（包括短标题、关键点等）
    - 超长段落按句子边界再分割，不吞字符或标点
    - 段落编号从 1 开始连续
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    raw_paragraphs = re.split(r"\n{2,}", normalized)

    paragraphs: list[str] = []
    for raw in raw_paragraphs:
        stripped = raw.strip()
        if not stripped:
            continue
        if len(stripped) <= settings.chunk_max_chars:
            paragraphs.append(stripped)
        else:
            sub_parts = _split_long_paragraph(stripped)
            paragraphs.extend(sub_parts)

    result = []
    for i, para in enumerate(paragraphs, start=1):
        result.append({
            "paragraph_number": i,
            "text": para,
        })

    return result


def _split_long_paragraph(text: str) -> list[str]:
    """将超长段落按句子边界分割，不丢失任何字符。"""
    parts: list[str] = []
    remaining = text

    while len(remaining) > settings.chunk_max_chars:
        cut_pos = _find_sentence_boundary(remaining, settings.chunk_max_chars)
        if cut_pos <= 0:
            cut_pos = settings.chunk_max_chars
        part = remaining[:cut_pos]
        if part:
            parts.append(part)
        remaining = remaining[cut_pos:]

    if remaining:
        parts.append(remaining)

    return parts if parts else [text]


def _find_sentence_boundary(text: str, max_len: int) -> int:
    """在 max_len 范围内从后向前找句子边界。

    返回切割位置（该位置之前的内容作为一段）。
    如果找不到边界，返回 0 让调用者按长度硬切。
    """
    search_range = text[:max_len]
    boundary_pattern = re.compile(r"(?<=[。！？.!?])\s*")

    last_match_end = 0
    for m in boundary_pattern.finditer(search_range):
        if m.start() > 0:
            last_match_end = m.end()

    if last_match_end > max_len * 0.3:
        return last_match_end

    comma_pattern = re.compile(r"(?<=[，,；;])\s*")
    for m in comma_pattern.finditer(search_range):
        if m.start() > 0:
            last_match_end = m.end()

    if last_match_end > max_len * 0.5:
        return last_match_end

    return 0
