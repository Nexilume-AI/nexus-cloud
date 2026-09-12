from django.test import SimpleTestCase

from apps.agents.runtime_runner import RuntimeMCPResult
from apps.agents.runtime_services import (
    _mcp_tool_result_succeeded,
    _task_result_failure_details,
)


class EdgeMobileErrorMappingTests(SimpleTestCase):
    def test_named_edge_error_event_is_not_a_success(self) -> None:
        result = RuntimeMCPResult(
            status_code=200,
            headers={"Content-Type": "text/event-stream"},
            body=(
                b"event: error\n"
                b'data: {"code":"MOBILE_BUSY",'
                b'"message":"Mobile is being controlled by another Run"}\n\n'
            ),
        )

        self.assertFalse(_mcp_tool_result_succeeded(result))

    def test_direct_edge_error_keeps_safe_code_and_message(self) -> None:
        self.assertEqual(
            _task_result_failure_details(
                {
                    "code": "MOBILE_BUSY",
                    "message": "Mobile is being controlled by another Run",
                }
            ),
            ("MOBILE_BUSY", "Mobile is being controlled by another Run"),
        )

    def test_direct_edge_error_rejects_unsafe_code(self) -> None:
        self.assertEqual(
            _task_result_failure_details(
                {"code": "not safe!", "message": "Agent runtime rejected the action"}
            ),
            ("MCP_TASK_FAILED", "Agent runtime rejected the action"),
        )
