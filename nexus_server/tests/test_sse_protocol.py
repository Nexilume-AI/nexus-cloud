"""Public library integration, not a second implementation of SSE parsing."""
import io
import json
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from httpx_sse import EventSource
from sse_starlette import JSONServerSentEvent, ServerSentEvent

from apps.gateway.provider_adapters import ProviderClientError, iter_openai_sse
from apps.gateway.sse_protocol import SSESizeError, _WireBudget, encode_data, encode_json, iter_events


class FragmentedBody(io.BytesIO):
    def __init__(self, body, width):
        super().__init__(body)
        self.width, self.read_sizes = width, []

    def read1(self, size):
        self.read_sizes.append(size)
        return super().read(min(size, self.width))


class SSELibraryTests(SimpleTestCase):
    def test_all_byte_splits_bom_newlines_unicode_and_multiline_data(self):
        payload = {"choices": [{"delta": {"content": "你好🙂\u2028\u2029"}}]}
        text = json.dumps(payload, ensure_ascii=False)
        split = text.index("[")
        for newline in (b"\n", b"\r\n", b"\r"):
            wire = b"\xef\xbb\xbf" + newline.join([
                b": ping", b"event: message", b"id: sample", b"retry: 2000",
                b"data: " + text[:split].encode(), b"data: " + text[split:].encode(),
                b"", b"data: [DONE]", b"", b"",
            ])
            for width in (1, 2, 3, 7, 64, 65536):
                with self.subTest(newline=newline, width=width):
                    body = FragmentedBody(wire, width)
                    events = list(iter_openai_sse(body))
                    self.assertEqual(events[0].raw, payload)
                    self.assertTrue(events[1].done)
                    self.assertEqual(len(events), 2)
                    self.assertTrue(body.closed)
                    self.assertLessEqual(max(body.read_sizes), 65536)

    def test_public_library_is_used_without_creating_an_http_client(self):
        original = EventSource.iter_sse
        with patch("apps.gateway.sse_protocol.EventSource.iter_sse", autospec=True, side_effect=original) as parser, \
                patch("httpx.Client", side_effect=AssertionError("must retain authorized connection")):
            events = list(iter_events(io.BytesIO(b"data: hello\n\n"), limit=128))
        parser.assert_called_once()
        self.assertEqual(events[0].data, "hello")

    def test_encoder_delegates_to_public_library(self):
        with patch.object(JSONServerSentEvent, "encode", autospec=True, return_value=b"json-library") as encode:
            self.assertEqual(encode_json({"ok": True}), "json-library")
            encode.assert_called_once()
        with patch.object(ServerSentEvent, "encode", autospec=True, return_value=b"sse-library") as encode:
            self.assertEqual(encode_data("[DONE]"), "sse-library")
            encode.assert_called_once()

    def test_library_field_rules_and_opaque_data_are_preserved(self):
        wire = b"event: message\nid: one\nretry: 100\nunknown: ignored\ndata:  keep leading space\ndata: second\n\n"
        event = list(iter_events(io.BytesIO(wire), limit=256))[0]
        self.assertEqual((event.event, event.id, event.retry), ("message", "one", 100))
        self.assertEqual(event.data, " keep leading space\nsecond")
        self.assertEqual(encode_data(event.data), "data:  keep leading space\ndata: second\n\n")

    def test_compatibility_apis_use_the_same_library_encoder(self):
        from apps.gateway.claude_compat import anthropic_sse
        from apps.gateway.openai_compat import response_sse
        for encode in (anthropic_sse, response_sse):
            with self.subTest(api=encode.__name__):
                wire = encode("response.delta", {"type": "response.delta", "text": "你好🙂\nline"})
                events = list(iter_events(io.BytesIO(wire.encode()), limit=1024))
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0].event, "response.delta")
                self.assertEqual(events[0].json()["text"], "你好🙂\nline")

    def test_indented_field_is_not_a_valid_sse_data_field(self):
        events = list(iter_events(io.BytesIO(b"  data: ignored\n\ndata: valid\n\n"), limit=128))
        self.assertEqual([event.data for event in events], ["valid"])

    def test_budget_is_independent_of_transport_boundaries(self):
        for newline in (b"\n", b"\r\n", b"\r"):
            wire = (b"data: " + b"x" * 20 + newline + newline) * 20
            for width in (1, 2, 3, 7, 10000):
                budget = _WireBudget(28)
                for offset in range(0, len(wire), width):
                    budget.check(wire[offset:offset + width])
            too_big = b"data: " + b"x" * 29 + newline + newline
            for width in (1, 2, 7, 10000):
                budget = _WireBudget(28)
                with self.assertRaises(SSESizeError):
                    for offset in range(0, len(too_big), width):
                        budget.check(too_big[offset:offset + width])

    @override_settings(NEXUS_PROVIDER_SSE_MAX_LINE_BYTES=128)
    def test_oversized_unterminated_data_is_rejected_before_unbounded_read(self):
        body = FragmentedBody(b"data: " + b"x" * 10000, 10)
        with self.assertRaises(ProviderClientError) as caught:
            list(iter_openai_sse(body))
        self.assertEqual(caught.exception.error_code, "PROVIDER_RESPONSE_TOO_LARGE")
        self.assertLess(len(body.read_sizes), 20)
        self.assertTrue(body.closed)

    @override_settings(NEXUS_PROVIDER_SSE_MAX_LINE_BYTES=128)
    def test_heartbeats_do_not_accumulate_into_a_stream_size_limit(self):
        wire = b": heartbeat\r\n\r\n" * 1000 + b'data: {"choices":[]}\n\ndata: [DONE]\n\n'
        events = list(iter_openai_sse(FragmentedBody(wire, 3)))
        self.assertEqual(events[0].raw, {"choices": []})
        self.assertTrue(events[1].done)

    def test_invalid_utf8_is_not_silently_replaced(self):
        for bad in (b"\xff", b"\xe4\xbd"):
            body = FragmentedBody(b'data: {"content":"' + bad + b'"}\n\n', 1)
            with self.assertRaises(ProviderClientError):
                list(iter_openai_sse(body))
            self.assertTrue(body.closed)

    def test_cancel_closes_connection_without_reading_next_event(self):
        first = b'data: {"choices":[]}\n\n'
        body = FragmentedBody(first + b"data: secret-not-needed\n\n", len(first))
        stream = iter_openai_sse(body)
        self.assertEqual(next(stream).raw, {"choices": []})
        stream.close()
        self.assertTrue(body.closed)
        self.assertEqual(len(body.read_sizes), 1)
