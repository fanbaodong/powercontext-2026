"""测试输入验证逻辑。"""
import sys
import os
import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models import DocumentImport, validate_safe_url
from app.services.document import _validate_extension
from app.config import settings


class TestDocumentImportValidation:
    def test_valid_import(self):
        doc = DocumentImport(
            project_id=1,
            title="测试文档",
            content="这是一段足够长的测试内容，用于验证导入功能是否正常工作。" * 3,
        )
        assert doc.title == "测试文档"
        assert doc.project_id == 1

    def test_empty_content_rejected(self):
        with pytest.raises(ValidationError):
            DocumentImport(
                project_id=1,
                title="测试",
                content="   ",
            )

    def test_empty_title_rejected(self):
        with pytest.raises(ValidationError):
            DocumentImport(
                project_id=1,
                title="",
                content="一些内容",
            )

    def test_title_too_long(self):
        with pytest.raises(ValidationError):
            DocumentImport(
                project_id=1,
                title="x" * 501,
                content="一些内容",
            )

    def test_author_max_length(self):
        with pytest.raises(ValidationError):
            DocumentImport(
                project_id=1,
                title="测试",
                author="x" * 256,
                content="一些内容",
            )


class TestUrlValidation:
    def test_valid_http_url(self):
        url = validate_safe_url("http://example.com")
        assert url == "http://example.com"

    def test_valid_https_url(self):
        url = validate_safe_url("https://example.com/path?q=1")
        assert url.startswith("https://")

    def test_reject_ftp(self):
        with pytest.raises(ValueError, match="仅允许"):
            validate_safe_url("ftp://example.com")

    def test_reject_javascript(self):
        with pytest.raises(ValueError, match="仅允许"):
            validate_safe_url("javascript:alert(1)")

    def test_reject_data_url(self):
        with pytest.raises(ValueError, match="仅允许"):
            validate_safe_url("data:text/html,<script>alert(1)</script>")

    def test_reject_empty(self):
        with pytest.raises(ValueError):
            validate_safe_url("")

    def test_none_returns_none(self):
        doc = DocumentImport(project_id=1, title="来源可选", content="笔记", source_url=None)
        assert doc.source_url is None

    def test_reject_too_long(self):
        with pytest.raises(ValueError, match="长度超限"):
            validate_safe_url("https://example.com/" + "a" * 2000)


class TestFileExtensionValidation:
    def test_txt_allowed(self):
        _validate_extension("notes.txt")

    def test_md_allowed(self):
        _validate_extension("README.md")

    def test_uppercase_allowed(self):
        _validate_extension("FILE.TXT")

    def test_pdf_rejected(self):
        with pytest.raises(ValueError, match="不支持"):
            _validate_extension("document.pdf")

    def test_exe_rejected(self):
        with pytest.raises(ValueError, match="不支持"):
            _validate_extension("malware.exe")

    def test_none_allowed(self):
        _validate_extension(None)


class TestFileSizeValidation:
    def test_normal_size_ok(self):
        DocumentImport(project_id=1, title="大小校验", content="x" * 1000)

    def test_empty_rejected(self):
        with pytest.raises(ValidationError):
            DocumentImport(project_id=1, title="大小校验", content="")

    def test_oversized_rejected(self):
        big = "x" * (settings.max_upload_size_bytes + 1)
        with pytest.raises(ValidationError, match="超过限制"):
            DocumentImport(project_id=1, title="大小校验", content=big)

    def test_utf8_size_counts_bytes(self):
        big = "汉" * (settings.max_upload_size_bytes // 3 + 1)
        with pytest.raises(ValidationError, match="超过限制"):
            DocumentImport(project_id=1, title="字节限制", content=big)
