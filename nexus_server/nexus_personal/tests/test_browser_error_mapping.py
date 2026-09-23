"""Portable Browser error mapping; no local or remote browser is started."""
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase
from apps.agents.browser_runtime import AttachedBrowserError, browser_delegate_operation


class BrowserErrorMappingTests(SimpleTestCase):
    def test_recoverable_runtime_errors_keep_codes_without_leaking_details(self):
        run = SimpleNamespace(id="run", caller_subject_hash="caller",
                              computer_binding=SimpleNamespace(connection=object()))
        session = SimpleNamespace(runtime_session_id="session")
        for code, status in (("BROWSER_UNAVAILABLE", 503), ("BROWSER_STALE_OBSERVATION", 409)):
            error = RuntimeError("sensitive-runtime-detail")
            error.default_code = code
            with self.subTest(code=code), \
                 patch("apps.agents.browser_runtime.get_browser_delegate_run", return_value=run), \
                 patch("apps.agents.browser_runtime._runtime_browser_session", return_value=session), \
                 patch("apps.workspaces.computer_runtime.execute_runtime_command", side_effect=error) as command:
                with self.assertRaises(AttachedBrowserError) as raised:
                    browser_delegate_operation(run_id="run", token="test-only", data={"operation": "observe"})
                self.assertEqual(raised.exception.default_code, code)
                self.assertEqual(raised.exception.status_code, status)
                self.assertNotIn("sensitive-runtime-detail", str(raised.exception.detail))
                self.assertEqual(command.call_count, 1)
