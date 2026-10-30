"""测试段落切块逻辑。"""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.chunking import chunk_content, _split_long_paragraph


class TestChunkContent:
    def test_basic_paragraphs(self):
        text = "这是第一段落的内容。\n\n这是第二段落的内容。\n\n这是第三段落的内容。"
        result = chunk_content(text)
        assert len(result) == 3
        assert result[0]["paragraph_number"] == 1
        assert result[1]["paragraph_number"] == 2
        assert result[2]["paragraph_number"] == 3
        assert "第一段落" in result[0]["text"]
        assert "第二段落" in result[1]["text"]

    def test_empty_text(self):
        result = chunk_content("")
        assert result == []

    def test_whitespace_only(self):
        result = chunk_content("   \n\n  \n\n   ")
        assert result == []

    def test_single_paragraph(self):
        text = "这是一个单独的段落，内容足够长以满足最小字符数要求。"
        result = chunk_content(text)
        assert len(result) == 1
        assert result[0]["paragraph_number"] == 1

    def test_short_paragraphs_kept(self):
        text = "短\n\n这是一个足够长的段落，包含了足够的字符数来满足最小的切块要求。"
        result = chunk_content(text)
        assert len(result) == 2
        assert result[0]["text"] == "短"
        assert result[0]["paragraph_number"] == 1
        assert "足够长" in result[1]["text"]
        assert result[1]["paragraph_number"] == 2

    def test_mixed_line_endings(self):
        text = "第一段内容\r\n\r\n第二段内容\r\n\r\n第三段内容足够长以通过过滤条件。"
        result = chunk_content(text)
        assert len(result) >= 2

    def test_paragraph_numbering_continuous(self):
        text = "短\n\n第一个长段落内容，需要足够长才能通过过滤。\n\n也短\n\n第二个长段落内容，同样需要足够长才能通过过滤条件。"
        result = chunk_content(text)
        assert len(result) == 4
        assert result[0]["paragraph_number"] == 1
        assert result[1]["paragraph_number"] == 2
        assert result[2]["paragraph_number"] == 3
        assert result[3]["paragraph_number"] == 4

    def test_long_paragraph_split(self):
        long_text = "句子一。" * 600
        assert len(long_text) > 2000
        result = chunk_content(long_text)
        assert len(result) >= 2
        for chunk in result:
            assert len(chunk["text"]) <= 2100

    def test_chinese_content(self):
        text = "学习方法有很多种。其中最为有效的包括主动回忆、间隔重复和交错练习。\n\n认知科学研究表明，多感官参与学习可以显著提高记忆效果。视觉、听觉和触觉的结合使用能够让大脑建立更多的神经连接。"
        result = chunk_content(text)
        assert len(result) == 2
        assert "主动回忆" in result[0]["text"]
        assert "认知科学" in result[1]["text"]


class TestSplitLongParagraph:
    def test_split_by_sentence(self):
        text = "这是第一句话。这是第二句话！这是第三句话？" + "这是补充内容。" * 400
        assert len(text) > 2000
        parts = _split_long_paragraph(text)
        assert len(parts) >= 2
        for part in parts:
            assert len(part) <= 2100

    def test_no_split_needed(self):
        text = "这是一个短段落。"
        parts = _split_long_paragraph(text)
        assert len(parts) == 1
        assert parts[0] == text
