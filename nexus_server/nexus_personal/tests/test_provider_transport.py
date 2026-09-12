"""The real HTTP fixture must not hide handler failures behind green tests."""
import contextlib
import http.client
import io
import json
import threading
import unittest
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from .provider_http_fixture import ProviderHTTPFixture


class ProviderFixtureTransportTests(ProviderHTTPFixture, SimpleTestCase):
    def handler(self, writer):
        handler = object.__new__(self.upstream.RequestHandlerClass)
        handler.server = self.upstream
        handler.path = "/v1/chat/completions"
        body = json.dumps({"model": "personal-model", "stream": True}).encode()
        handler.rfile = io.BytesIO(body)
        handler.wfile = writer
        handler.headers = {"Content-Length": str(len(body)), "Content-Type": "application/json",
                           "Authorization": "Bearer local-provider-test-key"}
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.close_connection = False
        return handler

    def test_disconnect_at_first_or_final_sse_write_stops_without_retry(self):
        for error in (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            for preceding_writes in (0, 2):
                with self.subTest(error=error.__name__, preceding_writes=preceding_writes):
                    writer = Mock()
                    writer.write.side_effect = [1] * preceding_writes + [error("fixture-only-disconnect")]
                    handler = self.handler(writer)
                    handler.do_POST()
                    self.assertTrue(handler.close_connection)
                    self.assertEqual(writer.write.call_count, preceding_writes + 1)

    def test_unexpected_write_error_is_not_treated_as_client_disconnect(self):
        writer = Mock()
        writer.write.side_effect = OSError(9, "fixture-only-invalid-descriptor")
        with self.assertRaises(OSError) as raised:
            self.handler(writer).do_POST()
        self.assertEqual(raised.exception.errno, 9)

    def test_disconnect_during_flush_stops_remaining_frames(self):
        writer = Mock()
        writer.flush.side_effect = ConnectionResetError("fixture-only-disconnect")
        handler = self.handler(writer)
        handler.do_POST()
        self.assertTrue(handler.close_connection)
        self.assertEqual(writer.write.call_count, 1)
        self.assertEqual(writer.flush.call_count, 1)

    def test_active_request_cannot_be_mistaken_for_a_healthy_idle_fixture(self):
        entered = threading.Event()
        release = threading.Event()
        connection = http.client.HTTPConnection("127.0.0.1", self.upstream.server_port, timeout=3)

        def blocking_handler(handler):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("fixture barrier timed out")
            handler.reply({"ok": True})

        try:
            with patch.object(self.upstream.RequestHandlerClass, "do_POST", blocking_handler):
                connection.request("POST", "/", body=b"{}")
                self.assertTrue(entered.wait(3))
                with self.assertRaisesMessage(AssertionError, "Upstream fixture did not become idle"):
                    self.upstream.assert_healthy(timeout=0)
                release.set()
                self.assertEqual(json.loads(connection.getresponse().read()), {"ok": True})
                self.upstream.assert_healthy()
        finally:
            release.set()
            connection.close()

    def test_complete_error_empty_and_truncated_frames_keep_their_contract(self):
        for mode, expected_frames in (("complete", 3), ("error", 2), ("empty", 1), ("truncated", 1)):
            with self.subTest(mode=mode):
                type(self).gateway_stream_mode = mode
                writer = io.BytesIO()
                self.handler(writer).do_POST()
                frames = [frame for frame in writer.getvalue().split(b"\n\n") if frame]
                self.assertEqual(len(frames), expected_frames)
                self.assertEqual(b"data: [DONE]" in frames, mode != "truncated")
                if mode == "complete":
                    self.assertEqual(json.loads(frames[-2][6:])["usage"]["total_tokens"], 12)

    def test_real_background_handler_exception_fails_its_own_test_without_secret_output(self):
        class CrashingFixture(ProviderHTTPFixture, SimpleTestCase):
            def test_request(self):
                connection = http.client.HTTPConnection("127.0.0.1", self.upstream.server_port, timeout=3)
                try:
                    with patch.object(self.upstream.RequestHandlerClass, "do_POST",
                                      side_effect=RuntimeError("private-fixture-error-marker")):
                        # A handler crash may close before the body is sent or
                        # before response headers arrive. Windows can report an
                        # aborted/reset connection instead of HTTP EOF. These
                        # are the same expected disconnect, not a second error.
                        with self.assertRaises((http.client.RemoteDisconnected, ConnectionResetError, ConnectionAbortedError)):
                            connection.request("POST", "/", body=b"{}")
                            connection.getresponse()
                finally:
                    connection.close()

        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            result = unittest.TextTestRunner(stream=output).run(
                unittest.defaultTestLoader.loadTestsFromTestCase(CrashingFixture))
        self.assertFalse(result.wasSuccessful(), "background handler failures must fail the fixture's test")
        self.assertEqual(len(result.failures) + len(result.errors), 1)
        self.assertIn("Unexpected upstream handler errors", output.getvalue())
        self.assertNotIn("private-fixture-error-marker", output.getvalue())

    @contextlib.contextmanager
    def reported_disconnect(self, error_type):
        # Preserve the real request and crashing background handler. Normalize
        # only the client-visible disconnect so every OS outcome is exercised
        # deterministically, without replaying the request or hiding timeouts.
        def normalized(operation):
            def invoke(connection, *args, **kwargs):
                try:
                    return operation(connection, *args, **kwargs)
                except (http.client.RemoteDisconnected, ConnectionResetError, ConnectionAbortedError):
                    raise error_type('fixture-only-client-disconnect') from None
            return invoke

        with patch.object(http.client.HTTPConnection, 'request', normalized(http.client.HTTPConnection.request)), \
                patch.object(http.client.HTTPConnection, 'getresponse', normalized(http.client.HTTPConnection.getresponse)):
            yield

    def test_background_failure_is_single_for_each_native_disconnect(self):
        for error_type in (http.client.RemoteDisconnected, ConnectionResetError, ConnectionAbortedError):
            with self.subTest(disconnect=error_type.__name__), self.reported_disconnect(error_type):
                self.test_real_background_handler_exception_fails_its_own_test_without_secret_output()

    def test_unrelated_client_timeout_is_not_hidden_by_background_failure_check(self):
        with self.reported_disconnect(TimeoutError), self.assertRaisesRegex(AssertionError, '^2 != 1$'):
            self.test_real_background_handler_exception_fails_its_own_test_without_secret_output()
