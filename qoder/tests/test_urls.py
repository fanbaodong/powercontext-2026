"""测试 URL 安全处理。"""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models import validate_safe_url


class TestSafeUrl:
    """确保来源URL只允许安全协议。"""

    def test_http_ok(self):
        assert validate_safe_url("http://example.com") == "http://example.com"

    def test_https_ok(self):
        assert validate_safe_url("https://example.com") == "https://example.com"

    def test_https_with_path(self):
        url = "https://example.com/path/to/resource?q=test#section"
        assert validate_safe_url(url) == url

    def test_reject_file_protocol(self):
        with pytest.raises(ValueError):
            validate_safe_url("file:///etc/passwd")

    def test_reject_javascript_protocol(self):
        with pytest.raises(ValueError):
            validate_safe_url("javascript:alert(document.cookie)")

    def test_reject_data_protocol(self):
        with pytest.raises(ValueError):
            validate_safe_url("data:text/html;base64,PHNjcmlwdD4=")

    def test_reject_vbscript_protocol(self):
        with pytest.raises(ValueError):
            validate_safe_url("vbscript:MsgBox")

    def test_strip_whitespace(self):
        url = "  https://example.com  "
        assert validate_safe_url(url) == "https://example.com"

    def test_case_insensitive_scheme(self):
        url = "HTTPS://Example.Com"
        assert validate_safe_url(url) == url

    def test_reject_relative_url(self):
        with pytest.raises(ValueError):
            validate_safe_url("/relative/path")

    def test_reject_bare_domain(self):
        with pytest.raises(ValueError):
            validate_safe_url("example.com")
