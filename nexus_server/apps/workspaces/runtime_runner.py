from __future__ import annotations

from typing import Any
from .connection_core import RunnerResult, WorkspaceError
from .models import WorkspaceConnection, WorkspaceTerminalSession


def is_windows_os_name(os_name: str) -> bool:
    return any(marker in os_name for marker in ["windows", "mingw", "msys", "cygwin", "win32"])

class ComputerRuntimeTerminalChannel:
    def __init__(self, *, connection: WorkspaceConnection, runtime_session_id: str) -> None:
        self.connection = connection
        self.runtime_session_id = runtime_session_id
        self.closed = False

    def _execute(self, operation: str, payload: dict[str, Any], timeout: int = 5) -> dict[str, Any]:
        from .computer_runtime import execute_runtime_command

        return execute_runtime_command(
            connection=self.connection,
            operation=operation,
            required_scope="command.execute",
            payload={"session_id": self.runtime_session_id, **payload},
            timeout_seconds=timeout,
        )

    def read(self, timeout: float = 0.25) -> str:
        if self.closed:
            return ""
        result = self._execute("terminal.read", {"timeout_seconds": max(0.05, min(float(timeout), 1.0))}, timeout=2)
        return str(result.get("data") or "")

    def write(self, data: str) -> None:
        if not self.closed:
            self._execute("terminal.write", {"data": str(data)}, timeout=5)

    def resize(self, *, cols: int, rows: int) -> None:
        if not self.closed:
            self._execute("terminal.resize", {"cols": int(cols), "rows": int(rows)}, timeout=5)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self._execute("terminal.close", {}, timeout=5)
        except Exception:
            return


class ComputerRuntimeWorkspaceRunner:
    """Workspace runner backed by the caller's outbound Nexus Computer Runtime."""

    def _execute(
        self,
        *,
        connection: WorkspaceConnection,
        operation: str,
        required_scope: str,
        payload: dict[str, Any],
        timeout_seconds: int = 30,
        runtime_context: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        from .computer_runtime import execute_runtime_command

        context = runtime_context or {}
        return execute_runtime_command(
            connection=connection,
            operation=operation,
            required_scope=required_scope,
            payload=payload,
            timeout_seconds=timeout_seconds,
            display_run_id=context.get("display_run_id") or None,
            caller_subject_hash=context.get("caller_subject_hash") or "",
            idempotency_key=context.get("idempotency_key") or "",
        )

    def test(self, *, connection: WorkspaceConnection) -> RunnerResult:
        from .computer_runtime import runtime_test

        return runtime_test(connection)

    def open_terminal(self, *, session: WorkspaceTerminalSession) -> ComputerRuntimeTerminalChannel:
        device = getattr(session.connection, "runtime_device", None)
        if device is not None and int((device.capabilities or {}).get("terminal.stream.v1") or 0) >= 1:
            from .computer_runtime_asgi import open_runtime_terminal_stream, runtime_terminal_stream_is_local

            if runtime_terminal_stream_is_local(device_id=str(device.id), generation=int(device.generation)):
                return open_runtime_terminal_stream(
                    connection=session.connection,
                    shell=resolve_terminal_shell(session=session),
                    workspace_root=session.authorized_root or session.connection.workspace_root,
                    cols=int(session.cols),
                    rows=int(session.rows),
                )
        result = self._execute(
            connection=session.connection,
            operation="terminal.open",
            required_scope="command.execute",
            payload={
                "shell": resolve_terminal_shell(session=session),
                "cols": int(session.cols),
                "rows": int(session.rows),
                "workspace_root": session.authorized_root or session.connection.workspace_root,
            },
            timeout_seconds=15,
        )
        runtime_session_id = str(result.get("session_id") or "")
        if not runtime_session_id:
            raise WorkspaceError("Computer Runtime did not create a terminal session.")
        return ComputerRuntimeTerminalChannel(connection=session.connection, runtime_session_id=runtime_session_id)

    def ensure_directory(self, *, connection: WorkspaceConnection, path: str, runtime_context: dict[str, str] | None = None) -> None:
        self._execute(connection=connection, operation="workspace.ensure_directory", required_scope="files.write", payload={"path": path, "workspace_root": connection.workspace_root}, runtime_context=runtime_context)

    def list_files(self, *, connection: WorkspaceConnection, path: str, root: str, runtime_context: dict[str, str] | None = None) -> dict[str, Any]:
        return self._execute(connection=connection, operation="workspace.list_files", required_scope="files.list", payload={"path": path, "workspace_root": root}, runtime_context=runtime_context)

    def read_file(self, *, connection: WorkspaceConnection, path: str, max_bytes: int, runtime_context: dict[str, str] | None = None) -> dict[str, Any]:
        return self._execute(connection=connection, operation="workspace.read_file", required_scope="files.read", payload={"path": path, "workspace_root": connection.workspace_root, "max_bytes": int(max_bytes)}, runtime_context=runtime_context)

    def write_file(self, *, connection: WorkspaceConnection, path: str, content: str, runtime_context: dict[str, str] | None = None) -> dict[str, Any]:
        return self._execute(connection=connection, operation="workspace.write_file", required_scope="files.write", payload={"path": path, "workspace_root": connection.workspace_root, "content": str(content)}, runtime_context=runtime_context)

    def run_command(self, *, connection: WorkspaceConnection, cwd: str, command: str, timeout_seconds: int, output_max_bytes: int, runtime_context: dict[str, str] | None = None) -> dict[str, Any]:
        return self._execute(
            connection=connection,
            operation="command.execute",
            required_scope="command.execute",
            payload={"cwd": cwd, "workspace_root": connection.workspace_root, "command": command, "timeout_seconds": int(timeout_seconds), "output_max_bytes": int(output_max_bytes)},
            timeout_seconds=int(timeout_seconds) + 5,
            runtime_context=runtime_context,
        )

    def detect_tools(self, *, connection: WorkspaceConnection) -> dict[str, Any]:
        return self._execute(connection=connection, operation="tool_setup.detect", required_scope="tool.setup", payload={}, timeout_seconds=30)

    def read_codex_config(self, *, connection: WorkspaceConnection) -> dict[str, Any]:
        return self._execute(connection=connection, operation="tool_setup.read_codex_config", required_scope="tool.setup", payload={})

    def write_codex_config(self, *, connection: WorkspaceConnection, content: str) -> dict[str, Any]:
        return self._execute(connection=connection, operation="tool_setup.write_codex_config", required_scope="tool.setup", payload={"content": content})

    def backup_codex_config(self, *, connection: WorkspaceConnection, content: str) -> str:
        result = self._execute(connection=connection, operation="tool_setup.backup_codex_config", required_scope="tool.setup", payload={"content": content})
        return str(result.get("backup_path") or "")

    def rollback_codex_config(self, *, connection: WorkspaceConnection) -> dict[str, Any]:
        return self._execute(connection=connection, operation="tool_setup.rollback_codex_config", required_scope="tool.setup", payload={})


def resolve_terminal_shell(*, session: WorkspaceTerminalSession) -> str:
    facts = cached_workspace_facts(connection=session.connection)
    if is_windows_workspace_target(facts):
        return WorkspaceTerminalSession.SHELL_POWERSHELL
    if session.shell != WorkspaceTerminalSession.SHELL_AUTO:
        return session.shell
    shell = str(facts.get("shell") or "").lower()
    if shell == "bash":
        return WorkspaceTerminalSession.SHELL_BASH
    return WorkspaceTerminalSession.SHELL_SH


def cached_workspace_facts(*, connection: WorkspaceConnection) -> dict[str, Any]:
    facts = connection.metadata.get("last_facts", {}) if isinstance(connection.metadata, dict) else {}
    return facts if isinstance(facts, dict) else {}


def is_windows_workspace_target(facts: dict[str, Any]) -> bool:
    os_name = str(facts.get("os") or "").lower()
    shell = str(facts.get("shell") or "").lower()
    return is_windows_os_name(os_name) or shell in {"cmd", "powershell"}
