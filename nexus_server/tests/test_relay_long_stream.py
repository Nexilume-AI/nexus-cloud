import io
import unittest
from apps.agents.runtime_runner import _relay_stream_result

class Response(io.BytesIO):
    status = 200
    headers = {"Content-Type": "text/event-stream"}

class RelayLongStreamTests(unittest.TestCase):
    def test_many_events_exceed_old_cumulative_limit(self):
        response = Response(b": heartbeat\n\n" * 100000 + b'event: result\ndata: {"result":{"ok":true}}\n\n')
        chunks = _relay_stream_result(response, "test").chunks
        self.assertEqual(sum(1 for _ in chunks), 100001)
        self.assertTrue(response.closed)

    def test_unterminated_line_is_bounded(self):
        response = Response(b"data: " + b"x" * 1048600)
        with self.assertRaisesRegex(ValueError, "event exceeds bound"):
            list(_relay_stream_result(response, "test").chunks)
        self.assertTrue(response.closed)

    def test_missing_terminal_result_is_rejected(self):
        response = Response(b": heartbeat\n\n")
        with self.assertRaisesRegex(ValueError, "complete result"):
            list(_relay_stream_result(response, "test").chunks)
        self.assertTrue(response.closed)
