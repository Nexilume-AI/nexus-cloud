from contextlib import ExitStack
from unittest.mock import patch
from types import SimpleNamespace
from django.test import SimpleTestCase
from apps.agents.runtime_runner import RuntimeMCPResult
from apps.agents.runtime_services import AgentPolicyDenied, AgentStreamingNotSupported, call_runtime_mcp_stream

class RelayMCPNegotiationTests(SimpleTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.runtime = SimpleNamespace(runtime_kind="openwrt_relay")
        for name, value in (
            ("get_tenant_from_request", "tenant"),
            ("get_runtime_use_agent", "agent"),
            ("get_runtime_deployment", self.runtime),
        ):
            self.stack.enter_context(patch("apps.agents.runtime_services." + name, return_value=value))
        self.policy = self.stack.enter_context(patch("apps.agents.runtime_services.enforce_api_key_agent_policy"))
        self.regular = self.stack.enter_context(patch("apps.agents.runtime_services.call_runtime_mcp"))
        # Streaming setup must never run for a Relay JSON response: it can
        # create sessions, invocations and billing reservations.
        self.streaming_setup = self.stack.enter_context(patch(
            "apps.agents.runtime_services.prepare_mcp_session_request",
            side_effect=AgentStreamingNotSupported(),
        ))

    def test_dual_accept_posts_use_json_once_before_streaming_setup(self):
        for method, status, body in (
            ("initialize", 200, b'{"jsonrpc":"2.0","id":1,"result":{}}'),
            ("notifications/initialized", 202, b""),
        ):
            with self.subTest(method=method):
                self.regular.reset_mock()
                response_headers = {"Content-Type": "application/json", "Mcp-Session-Id": "session"}
                self.regular.return_value = RuntimeMCPResult(status, response_headers, body)
                request = SimpleNamespace()
                payload = ('{"jsonrpc":"2.0","method":"' + method + '"}').encode()
                headers = {"ACCEPT": "application/json, text/event-stream"}
                result = call_runtime_mcp_stream(request=request, agent_id="agent", method="POST", body=payload, headers=headers)
                self.assertEqual(result.status_code, status)
                self.assertEqual(result.headers, response_headers)
                self.assertEqual(b"".join(result.chunks), body)
                self.regular.assert_called_once_with(request=request, agent_id="agent", method="POST", body=payload, headers=headers)
                self.streaming_setup.assert_not_called()

    def test_sse_only_get_legacy_and_other_runtimes_keep_streaming_path(self):
        cases = (
            ("openwrt_relay", "POST", "", "text/event-stream"),
            ("openwrt_relay", "POST", "", "application/json;q=0, text/event-stream"),
            ("openwrt_relay", "GET", "", "application/json, text/event-stream"),
            ("openwrt_relay", "POST", "/sse", "application/json, text/event-stream"),
            ("openwrt_ipv6", "POST", "", "application/json, text/event-stream"),
            ("docker", "POST", "", "application/json, text/event-stream"),
        )
        for kind, method, path, accept in cases:
            with self.subTest(kind=kind, method=method, path=path, accept=accept):
                self.runtime.runtime_kind = kind
                with self.assertRaises(AgentStreamingNotSupported):
                    call_runtime_mcp_stream(request=SimpleNamespace(), agent_id="agent", method=method,
                                            body=b"{}", headers={"aCcEpT": accept}, upstream_path=path)
                self.regular.assert_not_called()

    def test_dual_accept_tool_call_uses_streaming_setup(self):
        with self.assertRaises(AgentStreamingNotSupported):
            call_runtime_mcp_stream(request=SimpleNamespace(), agent_id="agent", method="POST",
                body=b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"test"}}',
                headers={"Accept":"application/json, text/event-stream"})
        self.regular.assert_not_called()

    def test_policy_denial_does_not_fall_back_to_json(self):
        self.policy.side_effect = AgentPolicyDenied()
        with self.assertRaises(AgentPolicyDenied):
            call_runtime_mcp_stream(request=SimpleNamespace(), agent_id="agent", method="POST",
                                    body=b"{}", headers={"Accept": "application/json, text/event-stream"})
        self.regular.assert_not_called()
        self.streaming_setup.assert_not_called()


class RelaySSETranslationTests(SimpleTestCase):
    def response(self, parts):
        response = SimpleNamespace(status=200, headers={"Content-Type":"text/event-stream"}, closed=False)
        pending = iter(parts)
        response.read = response.read1 = lambda size: next(pending, b"")
        response.close = lambda: setattr(response, "closed", True)
        return response

    def test_progress_arrives_before_result_and_preserves_native_content(self):
        from apps.agents.runtime_runner import _relay_stream_result
        response = self.response([b'event: progress\ndata: {"progress":0}\n\n',
            b'event: result\ndata: {"result":{"nexus_mcp_result_version":1,"result":{"content":[{"type":"text","text":"done"}],"isError":false}}}\n\n'])
        result = _relay_stream_result(response, 12, "progress-token")
        iterator = iter(result.chunks)
        self.assertIn(b'notifications/progress', next(iterator))
        self.assertFalse(response.closed)
        packet = next(iterator)
        self.assertIn(b'"id":12', packet)
        self.assertIn(b'"text":"done"', packet)
        self.assertEqual(list(iterator), [])
        self.assertTrue(response.closed)

    def test_incomplete_stream_fails_and_closes(self):
        from apps.agents.runtime_runner import _relay_stream_result
        response = self.response([b': ping\n\n'])
        with self.assertRaisesRegex(ValueError, 'complete result'):
            list(_relay_stream_result(response, 1).chunks)
        self.assertTrue(response.closed)
