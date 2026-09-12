import json
from http.client import IncompleteRead
from types import SimpleNamespace
from unittest import mock
from urllib.error import URLError

from django.test import SimpleTestCase
from rest_framework import exceptions

from apps.agents.run_failures import describe_failure, failure_payload
from apps.agents.runtime_services import _task_exception_details, _task_result_failure_details
from apps.agents.runtime_runner import AgentRuntimeConnectionFailed, OpenWrtIPv6RuntimeRunner, _http_request


class RunFailureTests(SimpleTestCase):
    def test_five_domains_and_recovery_actions(self):
        cases = [
            ("HANDLER_FAILED", "agent", "check_status"),
            ("AGENT_RUNTIME_NOT_AVAILABLE", "agent", "check_status"),
            ("RUN_CONTEXT_EXCHANGE_FAILED", "cloud", "check_status"),
            ("RUN_CONTEXT_TLS_FAILED", "cloud", "check_status"),
            ("WORKER_LOST_OUTCOME_UNKNOWN", "cloud", "check_status"),
            ("COMPUTER_RUNTIME_OFFLINE", "computer", "manage_computer"),
            ("WORKSPACE_UNAVAILABLE", "computer", "manage_computer"),
            ("BROWSER_PERMISSION_REQUIRED", "browser", "manage_computer"),
            ("BROWSER_SESSION_LOST", "browser", "new_run"),
            ("BROWSER_ACTION_FAILED", "browser", "view_browser"),
            ("TOOL_EXECUTION_FAILED", "unknown", "check_status"),
            ("MOBILE_BUSY", "mobile", "manage_mobile"),
            ("MOBILE_ACTION_FAILED", "mobile", "manage_mobile"),
            ("MOBILE_ACTION_REJECTED", "mobile", "continue_chat"),
            ("MOBILE_ACTION_CANCELLED", "mobile", "continue_chat"),
            ("MCP_TASK_FAILED", "unknown", "check_status"),
        ]
        for code, domain, action in cases:
            with self.subTest(code=code):
                result = describe_failure(code)
                self.assertEqual(result["domain"], domain)
                self.assertIn(action, result["actions"])
                self.assertFalse(result["automatic_retry"])

    def test_unknown_codes_are_not_guessed_from_error_text(self):
        for value in [-32004, None, {}, "Bearer private-token", "<script>", "BROWSER_FAKE_UNKNOWN"]:
            result = describe_failure(value)
            self.assertEqual(result["domain"], "unknown")
            self.assertNotIn("private-token", json.dumps(result))
            self.assertTrue(result["outcome_unknown"])

    def test_current_turn_only_and_no_sensitive_diagnostics(self):
        task = SimpleNamespace(error_code="COMPUTER_RUNTIME_OFFLINE", result_json={
            "message": "Authorization: Bearer secret", "url": "https://private.internal", "password": "secret"})
        for status in ["running", "input_required", "completed", "cancelled"]:
            self.assertIsNone(failure_payload(SimpleNamespace(status=status, execution_task=task)))
        result = failure_payload(SimpleNamespace(status="failed", execution_task=task))
        self.assertEqual(result["domain"], "computer")
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn("private.internal", json.dumps(result))

    def test_direct_gateway_and_structured_exception_preserve_source(self):
        for code in ["BROWSER_SESSION_LOST", "COMPUTER_RUNTIME_OFFLINE", "RUN_CONTEXT_TLS_FAILED", "HANDLER_FAILED"]:
            for data in [{"code": code}, {"gatewayBody": {"code": code}}]:
                with self.subTest(code=code, data=data):
                    actual, _ = _task_result_failure_details({"error": {"code": -32004, "message": "Invocation failed", "data": data}})
                    self.assertEqual(actual, code)
            actual, _ = _task_exception_details(exceptions.APIException({"code": code, "message": "private"}))
            self.assertEqual(actual, code)

    def test_nested_gateway_error_preserves_source(self):
        actual, _ = _task_result_failure_details({
            "error": {
                "code": -32000,
                "message": "Invocation failed",
                "data": {
                    "gatewayBody": {
                        "error": {
                            "code": "AGENT_RUNTIME_CONNECTION_FAILED",
                            "message": "private upstream diagnostic",
                        }
                    }
                },
            }
        })
        self.assertEqual(actual, "AGENT_RUNTIME_CONNECTION_FAILED")

    def test_generic_tool_error_does_not_claim_a_business_or_input_failure(self):
        result = {"isError": True, "content": [{"type": "text", "text": "Private task details"}]}
        for payload in [result, {"result": result}]:
            code, message = _task_result_failure_details(payload)
            self.assertEqual(code, "TOOL_EXECUTION_FAILED")
            self.assertEqual(describe_failure(code)["domain"], "unknown")
            self.assertNotIn("Private task", message)
        code, _ = _task_result_failure_details({"error": {"code": -32004}})
        self.assertEqual(describe_failure(code)["domain"], "unknown")

    def test_hosted_mcp_safe_cause_is_preserved_but_prose_is_not(self):
        for code in ("RUN_CONTEXT_TLS_FAILED", "BROWSER_SESSION_LOST", "COMPUTER_RUNTIME_OFFLINE"):
            result = {"isError": True, "content": [{"type": "text", "text": "Bearer secret"}],
                      "_meta": {"nexus": {"failure": {"code": code, "message": "private endpoint"}}}}
            for payload in (result, {"result": result}):
                actual, message = _task_result_failure_details(payload)
                self.assertEqual(actual, code)
                self.assertNotIn("secret", message)
                self.assertNotIn("private endpoint", message)
        self.assertEqual(describe_failure("RUN_CONTEXT_ORIGIN_MISMATCH")["domain"], "cloud")

    def test_openwrt_transport_failure_is_structured_and_sanitized(self):
        connection = mock.Mock()
        connection.request.side_effect = OSError("private endpoint and credential")
        runner = OpenWrtIPv6RuntimeRunner()
        with mock.patch.object(runner, "_connection", return_value=(connection, "/mcp", {})):
            with self.assertRaises(AgentRuntimeConnectionFailed) as result:
                runner.call_mcp(deployment=mock.Mock(), method="POST", headers={}, body=b"{}")
        self.assertEqual(_task_exception_details(result.exception)[0], "AGENT_RUNTIME_CONNECTION_FAILED")
        self.assertNotIn("private endpoint", str(result.exception))
        connection.close.assert_called_once()

    def test_openwrt_incomplete_response_is_a_structured_connection_failure(self):
        connection = mock.Mock()
        response = connection.getresponse.return_value
        response.status = 200
        response.getheaders.return_value = [("Content-Type", "application/json")]
        response.read.side_effect = IncompleteRead(b'{"jsonrpc":"2.0"', 128)
        runner = OpenWrtIPv6RuntimeRunner()
        with mock.patch.object(runner, "_connection", return_value=(connection, "/mcp", {})):
            with self.assertRaises(AgentRuntimeConnectionFailed) as result:
                runner.call_mcp(deployment=mock.Mock(), method="POST", headers={}, body=b"{}")
        self.assertEqual(_task_exception_details(result.exception)[0], "AGENT_RUNTIME_CONNECTION_FAILED")
        self.assertNotIn("jsonrpc", str(result.exception))
        connection.close.assert_called_once()

    def test_docker_transport_failure_does_not_expose_endpoint(self):
        with mock.patch("apps.agents.runtime_runner.urlopen", side_effect=URLError("secret endpoint")):
            with self.assertRaises(AgentRuntimeConnectionFailed):
                _http_request(url="http://agent.test/mcp", method="POST", headers={}, body=b"{}", timeout=1)

    def test_lost_browser_offers_fresh_run_not_session_replay(self):
        result = describe_failure("BROWSER_SESSION_LOST")
        self.assertNotIn("continue_chat", result["actions"])
        self.assertIn("cannot be restored", result["recovery_hint"])
        self.assertTrue(result["outcome_unknown"])

    def test_declined_mobile_action_does_not_blame_device_connection(self):
        result = describe_failure("MOBILE_ACTION_REJECTED")
        self.assertEqual(result["domain"], "mobile")
        self.assertIn("not approved", result["message"])
        self.assertEqual(result["actions"], ["continue_chat"])
        self.assertFalse(result["outcome_unknown"])

    def test_tls_recovery_never_suggests_disabling_verification(self):
        self.assertIn("Do not disable TLS verification", describe_failure("RUN_CONTEXT_TLS_FAILED")["recovery_hint"])

    def test_legacy_structured_cause_and_unconfirmed_cancel_are_not_lost(self):
        task = SimpleNamespace(error_code="MCP_TASK_FAILED", result_json={"data": {"gatewayBody": {"code": "BROWSER_SESSION_LOST"}}})
        self.assertEqual(failure_payload(SimpleNamespace(status="failed", execution_task=task))["domain"], "browser")
        result = describe_failure("CANCEL_UNCONFIRMED")
        self.assertTrue(result["outcome_unknown"])
        self.assertNotIn("continue_chat", result["actions"])

    def test_browser_broker_preserves_computer_dependency_failure(self):
        from apps.agents.browser_broker import browser_delegate_operation
        run = SimpleNamespace(id="run", caller_subject_hash="caller", computer_binding=SimpleNamespace(connection=object()))
        session = SimpleNamespace(runtime_session_id="session")
        for code in ["COMPUTER_RUNTIME_OFFLINE", "COMPUTER_RUNTIME_REVOKED", "COMPUTER_CAPABILITY_UNAVAILABLE"]:
            source = exceptions.APIException("secret connection diagnostic")
            source.default_code = code
            with mock.patch("apps.agents.browser_runtime.get_browser_delegate_run", return_value=run), \
                 mock.patch("apps.agents.browser_runtime._runtime_browser_session", return_value=session), \
                 mock.patch("apps.workspaces.computer_runtime.execute_runtime_command", side_effect=source):
                with self.assertRaises(exceptions.APIException) as result:
                    browser_delegate_operation(run_id="run", token="token", data={"operation": "observe"})
            self.assertEqual(result.exception.default_code, code)
            self.assertEqual(result.exception.get_codes(), code)
            self.assertEqual(describe_failure(code)["domain"], "computer")
            self.assertNotIn("secret", str(result.exception))
