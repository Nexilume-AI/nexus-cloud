"""Bound untrusted Provider bodies without changing successful API payloads."""
import io
import json
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from django.test import SimpleTestCase, override_settings

from apps.gateway.provider_adapters import OpenAICompatibleAdapter, ProviderClientError, iter_openai_sse, provider_error_message


class ProviderResponseSafetyTests(SimpleTestCase):
    @override_settings(NEXUS_GATEWAY_MOCK_HOSTS=set(), NEXUS_GATEWAY_FAIL_HOSTS=set(),
                       NEXUS_PROVIDER_RESPONSE_MAX_BYTES=128)
    def test_oversized_chat_body_is_rejected(self):
        deployment = SimpleNamespace(provider_account=SimpleNamespace(encrypted_key=""),
                                     endpoint="https://provider.example.test/v1", provider_runtime=None, model="test")
        body = io.BytesIO(json.dumps({"choices": [], "padding": "x" * 256}).encode())
        with patch("apps.gateway.provider_adapters.open_provider_url") as opener:
            opener.return_value.__enter__.return_value = body
            with self.assertRaises(ProviderClientError) as caught:
                OpenAICompatibleAdapter().chat_completions(deployment=deployment, payload={"messages": []})
        self.assertEqual(caught.exception.error_code, "PROVIDER_RESPONSE_TOO_LARGE")

    @override_settings(NEXUS_PROVIDER_SSE_MAX_LINE_BYTES=128)
    def test_oversized_sse_line_is_rejected(self):
        body = io.BytesIO(b'data: ' + json.dumps({"choices": [], "padding": "x" * 256}).encode() + b'\n\n')
        with self.assertRaises(ProviderClientError) as caught:
            list(iter_openai_sse(body))
        self.assertEqual(caught.exception.error_code, "PROVIDER_RESPONSE_TOO_LARGE")

    def test_upstream_error_text_never_echoes_arbitrary_credentials_or_prompts(self):
        marker = "credential-and-private-prompt-sentinel"
        for body in (marker, json.dumps({"error": {"message": marker}})):
            with self.subTest(body=body):
                self.assertNotIn(marker, provider_error_message(status_code=400, body=body))

    def test_normal_stream_preserves_unicode_usage_and_done(self):
        chunk = {"choices": [{"delta": {"content": "你好"}}], "usage": {"total_tokens": 3}}
        body = io.BytesIO(b': heartbeat\n\n' + b'data: ' + json.dumps(chunk).encode() + b'\n\ndata: [DONE]\n')
        events = list(iter_openai_sse(body))
        self.assertEqual(events[0].text_delta, "你好")
        self.assertEqual(events[0].usage, {"total_tokens": 3})
        self.assertTrue(events[1].done)

    def test_error_classification_and_retry_after_survive_body_redaction(self):
        for status, body, expected in ((401, b"private-marker", "PROVIDER_AUTH_FAILED"),
                (429, b"insufficient_quota private-marker", "PROVIDER_QUOTA_EXHAUSTED"),
                (429, b"private-marker", "PROVIDER_RATE_LIMITED"),
                (503, b"private-marker", "PROVIDER_UPSTREAM_UNAVAILABLE")):
            with self.subTest(status=status, expected=expected):
                failure = HTTPError("https://provider.example.test", status, "reason",
                                    {"Retry-After": "30"}, io.BytesIO(body))
                error = OpenAICompatibleAdapter().error_from_http_error(failure)
                self.assertEqual(error.error_code, expected)
                self.assertEqual(error.retry_after_seconds, 30)
                self.assertNotIn("private-marker", str(error))

    @override_settings(NEXUS_GATEWAY_MOCK_HOSTS=set(), NEXUS_GATEWAY_FAIL_HOSTS=set())
    def test_normal_chat_reads_with_a_bound_and_preserves_payload(self):
        class BoundedBody(io.BytesIO):
            def read(self, size=-1):
                if size < 0:
                    raise AssertionError("Unbounded Provider read")
                return super().read(size)

        raw = {"choices": [{"message": {"content": "你好"}}],
               "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}}
        deployment = SimpleNamespace(provider_account=SimpleNamespace(encrypted_key=""),
                                     endpoint="https://provider.example.test/v1", provider_runtime=None, model="test")
        with patch("apps.gateway.provider_adapters.open_provider_url") as opener:
            opener.return_value.__enter__.return_value = BoundedBody(json.dumps(raw).encode())
            result = OpenAICompatibleAdapter().chat_completions(deployment=deployment, payload={"messages": []})
        self.assertEqual(result.raw, raw)
        self.assertEqual(result.total_tokens, 5)

    @override_settings(NEXUS_PROVIDER_RESPONSE_MAX_BYTES=128)
    def test_health_response_is_also_bounded(self):
        deployment = SimpleNamespace(provider_runtime=None)
        with patch("apps.gateway.provider_adapters.open_provider_url") as opener:
            opener.return_value.__enter__.return_value = io.BytesIO(b"x" * 129)
            with self.assertRaises(ProviderClientError) as caught:
                OpenAICompatibleAdapter()._request_models(deployment=deployment,
                    account=SimpleNamespace(encrypted_key=""), base_url="https://provider.example.test")
        self.assertEqual(caught.exception.error_code, "PROVIDER_RESPONSE_TOO_LARGE")
