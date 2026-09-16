"""Runtime chooses its own automatic shell; explicit and SSH choices survive."""
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase
from apps.workspaces.models import WorkspaceConnection, WorkspaceTerminalSession
from apps.workspaces.runtime_runner import ComputerRuntimeWorkspaceRunner, resolve_terminal_shell


class RuntimeShellTests(SimpleTestCase):
    def session(self, *, shell="auto", os="Darwin", transport="runtime", device=None):
        connection = SimpleNamespace(connection_type=transport, metadata={"last_facts": {"os": os}},
                                     runtime_device=device, workspace_root="/tmp/workspace")
        return SimpleNamespace(connection=connection, shell=shell, authorized_root="/tmp/workspace", cols=100, rows=30)

    def test_auto_reaches_runtime_for_local_os_selection(self):
        for os in ("Darwin", "Linux", ""):
            with self.subTest(os=os):
                self.assertEqual(resolve_terminal_shell(session=self.session(os=os)), "auto")

    def test_explicit_posix_shell_and_legacy_ssh_are_preserved(self):
        for shell in ("bash", "sh"):
            self.assertEqual(resolve_terminal_shell(session=self.session(shell=shell)), shell)
        self.assertEqual(resolve_terminal_shell(session=self.session(transport="ssh")), "sh")
        self.assertEqual(resolve_terminal_shell(session=self.session(os="Windows")), "powershell")

    def test_rpc_terminal_open_preserves_auto(self):
        runner = ComputerRuntimeWorkspaceRunner()
        with patch.object(runner, "_execute", return_value={"session_id": "test-session"}) as execute:
            runner.open_terminal(session=self.session())
        self.assertEqual(execute.call_args.kwargs["payload"]["shell"], "auto")

    def test_live_stream_terminal_open_preserves_auto(self):
        device = SimpleNamespace(id="test-device", generation=1, capabilities={"terminal.stream.v1": 1})
        with patch("apps.workspaces.computer_runtime_asgi.runtime_terminal_stream_is_local", return_value=True), patch(
            "apps.workspaces.computer_runtime_asgi.open_runtime_terminal_stream"
        ) as open_stream:
            ComputerRuntimeWorkspaceRunner().open_terminal(session=self.session(device=device))
        self.assertEqual(open_stream.call_args.kwargs["shell"], "auto")
