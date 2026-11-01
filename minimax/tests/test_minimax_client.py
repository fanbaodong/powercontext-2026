"""Offline tests only: mock the HTTP boundary and use synthetic credentials."""

import copy
import io
import json
import os
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

from sdd_context.minimax_client import (
    DEFAULT_MODEL, MAX_RESPONSE_BYTES, MiniMaxError, _RejectRedirects, generate_change,
)


KEY = "synthetic-test-key-do-not-use"
URL = "https://api.minimax.cn/v1/chat/completions"
CHANGE = {"path": "tickets.py", "content": "def answer():\n    return 42\n", "summary": "Implement the rule."}


def valid_payload():
    return {
        "id": "request-123", "model": DEFAULT_MODEL, "created": 123456,
        "choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(CHANGE), "reasoning_content": "PRIVATE INTERNAL REASONING",
        }}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "prompt_tokens_details": {"cached_tokens": 3}},
        "base_resp": {"status_code": 0, "status_msg": "unneeded server message"},
    }


class MiniMaxClientTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"MINIMAX_API_KEY": KEY}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def run_payload(self, payload=None, *, url=URL, status=200, raw=None):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.geturl.return_value = url
        response.getcode.return_value = status
        response.read.return_value = raw if raw is not None else json.dumps(payload or valid_payload()).encode()
        opener = Mock()
        opener.open.return_value = response
        with patch("sdd_context.minimax_client.urllib.request.build_opener", return_value=opener):
            result = generate_change({"goal": "new rule"}, {"documents": ["rule v2"]}, "# source before\n")
        return result, opener

    def test_request_contains_task_context_source_and_keeps_safe_evidence(self):
        payload = valid_payload()
        payload["headers"] = {"authorization": KEY}
        payload["usage"]["unsafe"] = KEY
        payload["base_resp"]["status_msg"] = KEY
        result, opener = self.run_payload(payload)
        self.assertEqual(result["result"], CHANGE)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, URL)
        self.assertEqual(request.get_header("Authorization"), "Bearer " + KEY)
        sent = json.loads(request.data)
        user = json.loads(sent["messages"][1]["content"])
        self.assertEqual(user["source_text"], "# source before\n")
        self.assertEqual(user["retrieved_context"], {"documents": ["rule v2"]})
        self.assertEqual(user["task"], {"goal": "new rule"})
        self.assertEqual(sent["model"], DEFAULT_MODEL)
        self.assertEqual(sent["reasoning_effort"], "medium")
        self.assertEqual(result["request"]["body"], sent)
        self.assertEqual(result["response"]["base_resp"], {"status_code": 0})
        recorded = json.dumps(result)
        for forbidden in (KEY, "PRIVATE INTERNAL REASONING", "authorization", "reasoning_content", "status_msg"):
            self.assertNotIn(forbidden, recorded)
        opener.open.assert_called_once()

    def test_explicit_region_and_model_configuration(self):
        with patch.dict(os.environ, {"MINIMAX_BASE_URL": "https://api.minimax.io/v1/", "MINIMAX_MODEL": "MiniMax-M3"}):
            result, _ = self.run_payload(url="https://api.minimax.io/v1/chat/completions")
        self.assertEqual(result["request"]["body"]["model"], "MiniMax-M3")
        self.assertNotIn("reasoning_effort", result["request"]["body"])

    def test_final_markdown_fence_and_prefix_thinking_are_not_retained(self):
        payload = valid_payload()
        payload["choices"][0]["message"]["content"] = "<think>private material</think>\n```json\n" + json.dumps(CHANGE) + "\n```"
        result, _ = self.run_payload(payload)
        self.assertEqual(result["result"], CHANGE)
        self.assertNotIn("private material", json.dumps(result))

    def test_truncated_or_other_incomplete_output_is_rejected(self):
        for reason in ("length", "tool_calls", None):
            with self.subTest(reason=reason):
                payload = valid_payload()
                payload["choices"][0]["finish_reason"] = reason
                with self.assertRaises(MiniMaxError):
                    self.run_payload(payload)

    def test_empty_invalid_or_wrong_schema_output_is_rejected(self):
        for value in ("", "not json", "[]", '{"path":"tickets.py"}', '{"path":1,"content":"x","summary":"x"}',
                      '{"path":"tickets.py","content":"","summary":"x"}',
                      '{"path":"tickets.py","content":"x","summary":"x","extra":"discard?"}'):
            with self.subTest(value=value):
                payload = valid_payload()
                payload["choices"][0]["message"]["content"] = value
                with self.assertRaises(MiniMaxError):
                    self.run_payload(payload)

    def test_api_error_omits_server_message_and_key(self):
        payload = valid_payload()
        payload["base_resp"] = {"status_code": 1004, "status_msg": KEY}
        with self.assertRaises(MiniMaxError) as error:
            self.run_payload(payload)
        self.assertIn("1004", str(error.exception))
        self.assertNotIn(KEY, str(error.exception))

    def test_http_error_is_not_read_or_retried_and_omits_secret_body(self):
        body = io.BytesIO(KEY.encode())
        error = urllib.error.HTTPError(URL, 401, KEY, {"unsafe": KEY}, body)
        opener = Mock()
        opener.open.side_effect = error
        with patch("sdd_context.minimax_client.urllib.request.build_opener", return_value=opener):
            with self.assertRaises(MiniMaxError) as caught:
                generate_change({}, {}, "source")
        self.assertEqual(str(caught.exception), "MiniMax HTTP request failed (HTTP 401).")
        opener.open.assert_called_once()

    def test_network_error_omits_embedded_credentials(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.URLError(KEY)
        with patch("sdd_context.minimax_client.urllib.request.build_opener", return_value=opener):
            with self.assertRaises(MiniMaxError) as caught:
                generate_change({}, {}, "source")
        self.assertNotIn(KEY, str(caught.exception))
        opener.open.assert_called_once()

    def test_malicious_or_non_https_endpoints_are_rejected_before_request(self):
        for base in ("http://api.minimax.cn/v1", "https://evil.example/v1", "https://api.minimax.cn.evil.example/v1",
                     "https://user:secret@api.minimax.cn/v1", "https://api.minimax.cn:8443/v1",
                     "https://api.minimax.cn/v1?token=secret", "https://api.minimax.cn/v1#fragment"):
            with self.subTest(base=base), patch.dict(os.environ, {"MINIMAX_BASE_URL": base}):
                with patch("sdd_context.minimax_client.urllib.request.build_opener") as opener:
                    with self.assertRaises(MiniMaxError):
                        generate_change({}, {}, "source")
                    opener.assert_not_called()

    def test_redirect_handler_never_forwards_authorization(self):
        req = urllib.request.Request(URL, headers={"Authorization": "Bearer " + KEY})
        for target in ("https://evil.example/", "https://api.minimax.io/v1/chat/completions"):
            with self.subTest(target=target):
                with self.assertRaises(MiniMaxError):
                    _RejectRedirects().redirect_request(req, None, 307, "redirect", {}, target)

    def test_unexpected_final_url_is_rejected(self):
        with self.assertRaises(MiniMaxError):
            self.run_payload(url="https://evil.example/")

    def test_credential_echo_in_final_output_is_rejected(self):
        payload = valid_payload()
        changed = copy.deepcopy(CHANGE)
        changed["summary"] = KEY
        payload["choices"][0]["message"]["content"] = json.dumps(changed)
        with self.assertRaises(MiniMaxError) as error:
            self.run_payload(payload)
        self.assertNotIn(KEY, str(error.exception))

    def test_credential_in_input_is_rejected_before_request(self):
        with patch("sdd_context.minimax_client.urllib.request.build_opener") as opener:
            with self.assertRaises(MiniMaxError):
                generate_change({}, {}, KEY)
            opener.assert_not_called()

    def test_invalid_response_and_oversized_response_are_rejected(self):
        for raw in (b"not json", b"\xff", b"[]", b"x" * (MAX_RESPONSE_BYTES + 1)):
            with self.subTest(size=len(raw)), self.assertRaises(MiniMaxError):
                self.run_payload(raw=raw)

    def test_missing_key_does_not_make_request(self):
        with patch.dict(os.environ, {}, clear=True), patch("sdd_context.minimax_client.urllib.request.build_opener") as opener:
            with self.assertRaises(MiniMaxError):
                generate_change({}, {}, "source")
            opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
