"""Shared runner wire-contract units, not real WSS or hardware acceptance."""
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase
from apps.workspaces import runtime_runner as runner
from apps.workspaces.models import WorkspaceTerminalSession


class SharedRuntimeRunnerTests(SimpleTestCase):
    def test_shell_resolution_preserves_platform_and_explicit_selection(self):
        for facts, requested, expected in (
            ({"os": "Windows 11"}, "auto", "powershell"),
            ({"os": "Linux", "shell": "bash"}, "auto", "bash"),
            ({"os": "Darwin", "shell": "zsh"}, "auto", "sh"),
            ({"os": "Linux"}, "bash", "bash"),
        ):
            session = SimpleNamespace(connection=SimpleNamespace(metadata={"last_facts": facts}), shell=requested)
            self.assertEqual(runner.resolve_terminal_shell(session=session), expected)

    def test_file_command_and_tool_setup_keep_operation_scope_and_context(self):
        connection = SimpleNamespace(workspace_root="/workspace")
        target = runner.ComputerRuntimeWorkspaceRunner()
        context = {"display_run_id": "run", "caller_subject_hash": "caller", "idempotency_key": "command"}
        with patch("apps.workspaces.computer_runtime.execute_runtime_command", return_value={"content": "ok"}) as execute:
            self.assertEqual(target.read_file(connection=connection, path="input.txt", max_bytes=128, runtime_context=context), {"content": "ok"})
            self.assertEqual(execute.call_args.kwargs["required_scope"], "files.read")
            self.assertEqual(execute.call_args.kwargs["payload"], {"path": "input.txt", "workspace_root": "/workspace", "max_bytes": 128})
            for field, value in context.items():
                self.assertEqual(execute.call_args.kwargs[field], value)
            target.run_command(connection=connection, cwd="folder", command="pwd", timeout_seconds=12, output_max_bytes=256, runtime_context=context)
            self.assertEqual(execute.call_args.kwargs["required_scope"], "command.execute")
            self.assertEqual(execute.call_args.kwargs["timeout_seconds"], 17)
            target.read_codex_config(connection=connection)
            self.assertEqual(execute.call_args.kwargs["operation"], "tool_setup.read_codex_config")
            self.assertEqual(execute.call_args.kwargs["required_scope"], "tool.setup")

    def test_terminal_channel_preserves_unicode_and_close_is_idempotent(self):
        channel = runner.ComputerRuntimeTerminalChannel(connection=object(), runtime_session_id="session")
        with patch("apps.workspaces.computer_runtime.execute_runtime_command", return_value={"data": "中文\r\n"}) as execute:
            self.assertEqual(channel.read(), "中文\r\n")
            channel.write("输入\r")
            self.assertEqual(execute.call_args.kwargs["payload"], {"session_id": "session", "data": "输入\r"})
            channel.resize(cols=100, rows=40)
            self.assertEqual(execute.call_args.kwargs["operation"], "terminal.resize")
            channel.close()
            count = execute.call_count
            channel.close()
            channel.write("ignored")
            self.assertEqual(channel.read(), "")
            self.assertEqual(execute.call_count, count)
