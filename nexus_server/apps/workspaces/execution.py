from __future__ import annotations

import posixpath
import secrets
import threading
import time
from collections import deque
from typing import Any
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from apps.audit.services import log_audit
from apps.common.models import SoftDeleteModel
from apps.common.project_scope import scope_queryset_to_current_project
from apps.common.request_context import get_tenant_from_request
from apps.common.subjects import hash_token, request_subject
from .connection_core import WorkspaceError, WorkspaceNotFound, get_workspace_connection
from .models import WorkspaceConnection, WorkspaceTerminalSession
from .runtime_runner import ComputerRuntimeWorkspaceRunner
from .runner_dispatch import workspace_runner_for, workspace_runner_name_for
from .context_policy import scope_terminals, terminal_context_valid

_PERSISTENT_USER_TERMINALS: dict[str, PersistentUserTerminal] = {}
_PERSISTENT_USER_TERMINALS_LOCK = threading.RLock()

def list_terminal_sessions(*, request):
    tenant = get_tenant_from_request(request)
    subject = request_subject(request)
    queryset = (
        scope_queryset_to_current_project(
            WorkspaceTerminalSession.objects.filter(
                tenant=tenant,
                session_kind=WorkspaceTerminalSession.KIND_USER,
                connection__owner_subject_hash=subject.subject_hash,
            ),
            request,
        )
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .select_related("connection", "project")
        .order_by("-created_at")
    )
    return scope_terminals(request=request, queryset=queryset)


def get_terminal_session(*, request, session_id: str) -> WorkspaceTerminalSession:
    tenant = get_tenant_from_request(request)
    subject = request_subject(request)
    session = (
        WorkspaceTerminalSession.objects.filter(
            tenant=tenant,
            id=session_id,
            session_kind=WorkspaceTerminalSession.KIND_USER,
            connection__owner_subject_hash=subject.subject_hash,
        )
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .select_related("connection", "project")
        .first()
    )
    if session is None or not scope_terminals(request=request, queryset=WorkspaceTerminalSession.objects.filter(pk=session.pk)).exists():
        raise WorkspaceNotFound("Terminal session not found.")
    return session


def issue_terminal_websocket_ticket(*, request, session_id: str) -> dict[str, Any]:
    """Issue a short-lived, caller-bound ticket for one terminal WebSocket.

    Browser session cookies are not reliably forwarded by every WebSocket
    reverse proxy.  The authenticated HTTP request remains the authorization
    boundary; the socket receives only a one-time, narrowly scoped ticket.
    """

    session = get_terminal_session(request=request, session_id=session_id)
    if not terminal_context_valid(session):
        raise WorkspaceNotFound("Terminal session not found.")
    subject = request_subject(request)
    ttl_seconds = max(
        10,
        min(
            int(getattr(settings, "NEXUS_WORKSPACE_TERMINAL_TICKET_TTL_SECONDS", 60)),
            300,
        ),
    )
    ticket = secrets.token_urlsafe(32)
    now = int(time.time())
    with transaction.atomic():
        locked_session = WorkspaceTerminalSession.objects.select_for_update().get(id=session.id)
        metadata = dict(locked_session.metadata or {})
        active_tickets = [
            item
            for item in metadata.get("_websocket_tickets", [])
            if isinstance(item, dict) and int(item.get("expires_at") or 0) > now
        ]
        active_tickets.append(
            {
                "hash": hash_token(ticket),
                "expires_at": now + ttl_seconds,
                "owner_subject_hash": subject.subject_hash,
            }
        )
        metadata["_websocket_tickets"] = active_tickets[-8:]
        locked_session.metadata = metadata
        locked_session.save(update_fields=["metadata", "updated_at"])
    return {
        "ticket": ticket,
        "expires_in": ttl_seconds,
        "websocket_url": f"/ws/workspace-terminals/{session.id}/",
    }


@transaction.atomic
def create_terminal_session(*, request, data: dict[str, Any]) -> tuple[WorkspaceTerminalSession, bool]:
    tenant = get_tenant_from_request(request)
    connection = get_workspace_connection(request=request, connection_id=str(data["connection_id"]))
    # Serialize creates per Computer so a double-click or two browser requests
    # cannot manufacture parallel shells for the same caller-owned target.
    WorkspaceConnection.objects.select_for_update().get(id=connection.id)
    existing = (
        WorkspaceTerminalSession.objects.filter(
            tenant=tenant,
            connection=connection,
            session_kind=WorkspaceTerminalSession.KIND_USER,
            status__in=[WorkspaceTerminalSession.STATUS_CREATED, WorkspaceTerminalSession.STATUS_ACTIVE],
        )
        .order_by("-updated_at")
        .first()
    )
    if existing is not None:
        log_audit(
            request=request,
            action="workspaces.terminal.resume",
            actor=request.user,
            resource_type="workspace_terminal_session",
            resource_id=existing.id,
        )
        return existing, False
    session = WorkspaceTerminalSession.objects.create(
        tenant=tenant,
        project=connection.project,
        connection=connection,
        shell=data.get("shell") or WorkspaceTerminalSession.SHELL_AUTO,
        cols=data.get("cols") or 100,
        rows=data.get("rows") or 30,
        created_by=request.user,
        metadata=data.get("metadata", {}),
    )
    # Computer Runtime tool discovery is a durable remote command.  It must
    # not block creation of an interactive terminal (or occupy the ASGI sync
    # worker that the Runtime reply needs).  The existing explicit refresh
    # endpoint performs discovery when the Tool setup UI requests it.
    if connection.connection_type != WorkspaceConnection.TYPE_RUNTIME:
        update_terminal_tool_status(session=session)
    log_audit(request=request, action="workspaces.terminal.create", actor=request.user, resource_type="workspace_terminal_session", resource_id=session.id)
    return session, True


def refresh_terminal_tool_status(*, request, session_id: str) -> WorkspaceTerminalSession:
    session = get_terminal_session(request=request, session_id=session_id)
    update_terminal_tool_status(session=session)
    log_audit(request=request, action="workspaces.terminal.tools.detect", actor=request.user, resource_type="workspace_terminal_session", resource_id=session.id)
    return session


def update_terminal_tool_status(*, session: WorkspaceTerminalSession) -> dict[str, Any]:
    try:
        tool_status = workspace_runner_for(session.connection).detect_tools(connection=session.connection)
    except Exception as exc:
        tool_status = terminal_tool_status_error(error=str(exc), runner=workspace_runner_name_for(session.connection))
    session.metadata = {**session.metadata, "tool_status": tool_status}
    session.save(update_fields=["metadata", "updated_at"])
    return tool_status


@transaction.atomic
def close_terminal_session(*, request, session_id: str) -> WorkspaceTerminalSession:
    session = get_terminal_session(request=request, session_id=session_id)
    mark_terminal_session_closed(session=session)
    log_audit(request=request, action="workspaces.terminal.close", actor=request.user, resource_type="workspace_terminal_session", resource_id=session.id)
    return session


def list_workspace_files(*, connection: WorkspaceConnection, root: str, path: str = "", runtime_context: dict[str, str] | None = None) -> dict[str, Any]:
    remote_path = resolve_workspace_child_path(root=root, path=path)
    runner = workspace_runner_for(connection)
    if isinstance(runner, ComputerRuntimeWorkspaceRunner):
        return runner.list_files(connection=connection, path=remote_path, root=normalize_workspace_root(root), runtime_context=runtime_context)
    return runner.list_files(connection=connection, path=remote_path, root=normalize_workspace_root(root))


def ensure_workspace_directory(*, connection: WorkspaceConnection, path: str, runtime_context: dict[str, str] | None = None) -> None:
    """Create an authorized Workspace root before its first Run uses it."""

    runner = workspace_runner_for(connection)
    if isinstance(runner, ComputerRuntimeWorkspaceRunner):
        runner.ensure_directory(connection=connection, path=normalize_workspace_root(path), runtime_context=runtime_context)
    else:
        runner.ensure_directory(connection=connection, path=normalize_workspace_root(path))


def read_workspace_file(*, connection: WorkspaceConnection, root: str, path: str, runtime_context: dict[str, str] | None = None) -> dict[str, Any]:
    remote_path = resolve_workspace_child_path(root=root, path=path)
    runner = workspace_runner_for(connection)
    if isinstance(runner, ComputerRuntimeWorkspaceRunner):
        return runner.read_file(connection=connection, path=remote_path, max_bytes=agent_workspace_max_file_bytes(), runtime_context=runtime_context)
    return runner.read_file(connection=connection, path=remote_path, max_bytes=agent_workspace_max_file_bytes())


def write_workspace_file(*, connection: WorkspaceConnection, root: str, path: str, content: str, runtime_context: dict[str, str] | None = None) -> dict[str, Any]:
    if len(str(content).encode("utf-8")) > agent_workspace_max_file_bytes():
        raise WorkspaceError("Workspace file content is too large.")
    remote_path = resolve_workspace_child_path(root=root, path=path)
    runner = workspace_runner_for(connection)
    if isinstance(runner, ComputerRuntimeWorkspaceRunner):
        return runner.write_file(connection=connection, path=remote_path, content=str(content), runtime_context=runtime_context)
    return runner.write_file(connection=connection, path=remote_path, content=str(content))


def run_workspace_command(*, connection: WorkspaceConnection, root: str, cwd: str, command: str, timeout_seconds: int | None = None, runtime_context: dict[str, str] | None = None) -> dict[str, Any]:
    command_text = str(command or "").strip()
    if not command_text:
        raise WorkspaceError("Workspace command is required.")
    if len(command_text) > agent_workspace_max_command_chars():
        raise WorkspaceError("Workspace command is too long.")
    timeout_value = normalize_workspace_command_timeout(timeout_seconds)
    remote_cwd = resolve_workspace_child_path(root=root, path=cwd or ".")
    runner = workspace_runner_for(connection)
    kwargs = {
        "connection": connection,
        "cwd": remote_cwd,
        "command": command_text,
        "timeout_seconds": timeout_value,
        "output_max_bytes": agent_workspace_command_output_max_bytes(),
    }
    if isinstance(runner, ComputerRuntimeWorkspaceRunner):
        return runner.run_command(**kwargs, runtime_context=runtime_context)
    return runner.run_command(**kwargs)


def open_terminal_channel(*, session: WorkspaceTerminalSession):
    return workspace_runner_for(session.connection).open_terminal(session=session)


class PersistentTerminalUnavailable(RuntimeError):
    pass


class PersistentTerminalAttachment:
    """A browser attachment to one long-lived user terminal."""

    def __init__(self, terminal: PersistentUserTerminal) -> None:
        self._terminal = terminal
        self._cursor = 0
        self._detached = False

    def read(self, timeout: float = 0.25) -> str:
        if self._detached:
            return ""
        self._cursor, data = self._terminal.read_after(cursor=self._cursor, timeout=timeout)
        return data

    def write(self, data: str) -> None:
        if not self._detached:
            self._terminal.write(data)

    def resize(self, *, cols: int, rows: int) -> None:
        if not self._detached:
            self._terminal.resize(cols=cols, rows=rows)

    def close(self) -> None:
        # Closing a browser WebSocket only detaches the viewer. The underlying
        # SSH shell remains alive until the user explicitly ends the session.
        self._detached = True


class PersistentUserTerminal:
    MAX_REPLAY_BYTES = 2 * 1024 * 1024

    def __init__(self, *, channel) -> None:
        self._channel = channel
        self._condition = threading.Condition(threading.RLock())
        self._io_lock = threading.RLock()
        self._chunks: deque[tuple[int, str, int]] = deque()
        self._buffer_bytes = 0
        self._sequence = 0
        self._closed = False
        self._error = ""
        self._reader = threading.Thread(target=self._read_loop, name="nexus-user-terminal", daemon=True)
        self._reader.start()

    @property
    def available(self) -> bool:
        with self._condition:
            return not self._closed

    def attach(self) -> PersistentTerminalAttachment:
        return PersistentTerminalAttachment(self)

    def read_after(self, *, cursor: int, timeout: float) -> tuple[int, str]:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            while not any(seq > cursor for seq, _, _ in self._chunks) and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return cursor, ""
                self._condition.wait(timeout=remaining)
            chunks = [(seq, data) for seq, data, _ in self._chunks if seq > cursor]
            if chunks:
                return chunks[-1][0], "".join(data for _, data in chunks)
            if self._closed:
                raise PersistentTerminalUnavailable(self._error or "Terminal connection ended.")
            return cursor, ""

    def write(self, data: str) -> None:
        self._ensure_available()
        with self._io_lock:
            self._channel.write(data)

    def resize(self, *, cols: int, rows: int) -> None:
        self._ensure_available()
        with self._io_lock:
            self._channel.resize(cols=cols, rows=rows)

    def shutdown(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._condition.notify_all()
        try:
            with self._io_lock:
                self._channel.close()
        except Exception:
            return

    def _ensure_available(self) -> None:
        with self._condition:
            if self._closed:
                raise PersistentTerminalUnavailable(self._error or "Terminal connection ended.")

    def _read_loop(self) -> None:
        try:
            while self.available:
                data = self._channel.read(0.25)
                if data:
                    self._append(data)
                elif not terminal_channel_is_open(self._channel):
                    self._finish("Terminal connection ended.")
                    return
                time.sleep(0.02)
        except Exception:
            self._finish("Terminal connection ended unexpectedly.")

    def _append(self, data: str) -> None:
        size = len(data.encode("utf-8", errors="replace"))
        with self._condition:
            self._sequence += 1
            self._chunks.append((self._sequence, data, size))
            self._buffer_bytes += size
            while self._buffer_bytes > self.MAX_REPLAY_BYTES and len(self._chunks) > 1:
                _, _, removed_size = self._chunks.popleft()
                self._buffer_bytes -= removed_size
            self._condition.notify_all()

    def _finish(self, error: str) -> None:
        with self._condition:
            self._closed = True
            self._error = error
            self._condition.notify_all()
        try:
            with self._io_lock:
                self._channel.close()
        except Exception:
            return


def terminal_channel_is_open(channel) -> bool:
    raw_channel = getattr(channel, "channel", None)
    if raw_channel is not None and bool(getattr(raw_channel, "closed", False)):
        return False
    return not bool(getattr(channel, "closed", False))


def open_persistent_terminal(*, session: WorkspaceTerminalSession) -> PersistentTerminalAttachment:
    session_id = str(session.id)
    with _PERSISTENT_USER_TERMINALS_LOCK:
        terminal = _PERSISTENT_USER_TERMINALS.get(session_id)
        if terminal is not None and terminal.available:
            return terminal.attach()
        if terminal is not None:
            terminal.shutdown()
        terminal = PersistentUserTerminal(channel=open_terminal_channel(session=session))
        _PERSISTENT_USER_TERMINALS[session_id] = terminal
        return terminal.attach()


def close_persistent_terminal(*, session_id: str) -> None:
    with _PERSISTENT_USER_TERMINALS_LOCK:
        terminal = _PERSISTENT_USER_TERMINALS.pop(str(session_id), None)
    if terminal is not None:
        terminal.shutdown()


def mark_terminal_session_active(*, session: WorkspaceTerminalSession) -> None:
    session.status = WorkspaceTerminalSession.STATUS_ACTIVE
    session.started_at = session.started_at or timezone.now()
    session.last_error = ""
    session.save(update_fields=["status", "started_at", "last_error", "updated_at"])


def mark_terminal_session_failed(*, session: WorkspaceTerminalSession, error: str) -> None:
    session.refresh_from_db(fields=["status"])
    if session.status in {WorkspaceTerminalSession.STATUS_CLOSED, SoftDeleteModel.STATUS_DELETED}:
        return
    session.status = WorkspaceTerminalSession.STATUS_FAILED
    session.last_error = str(error)[:1024]
    session.ended_at = timezone.now()
    session.save(update_fields=["status", "last_error", "ended_at", "updated_at"])


def mark_terminal_session_closed(*, session: WorkspaceTerminalSession) -> None:
    session.status = WorkspaceTerminalSession.STATUS_CLOSED
    session.ended_at = timezone.now()
    session.save(update_fields=["status", "ended_at", "updated_at"])
    close_persistent_terminal(session_id=str(session.id))


def normalize_workspace_root(root: str) -> str:
    value = str(root or ".").strip().replace("\\", "/")
    return posixpath.normpath(value or ".")


def resolve_workspace_relative_path(*, base: str = ".", path: str = ".") -> str:
    """Resolve a Workspace-relative path without allowing root escape."""

    normalized_base = str(base or ".").strip().replace("\\", "/")
    raw_path = str(path or ".").strip().replace("\\", "/")
    if normalized_base.startswith("/") or normalized_base.startswith("~"):
        raise WorkspaceError("Workspace working directories must be relative to the authorized root.")
    if raw_path.startswith("/") or raw_path.startswith("~"):
        raise WorkspaceError("Workspace file paths must be relative to the authorized root.")
    candidate = posixpath.normpath(posixpath.join(normalized_base or ".", raw_path or "."))
    if candidate == ".." or candidate.startswith("../"):
        raise WorkspaceError("Workspace file path escapes the authorized root.")
    return "." if candidate in {"", "."} else candidate


def resolve_workspace_child_path(*, root: str, path: str) -> str:
    normalized_root = normalize_workspace_root(root)
    normalized_child = resolve_workspace_relative_path(path=path)
    if normalized_child in {".", ""}:
        return normalized_root
    return posixpath.normpath(posixpath.join(normalized_root, normalized_child))


def agent_workspace_max_file_bytes() -> int:
    return int(getattr(settings, "NEXUS_AGENT_WORKSPACE_MAX_FILE_BYTES", 1_048_576))


def agent_workspace_max_command_chars() -> int:
    return int(getattr(settings, "NEXUS_AGENT_WORKSPACE_MAX_COMMAND_CHARS", 8192))


def agent_workspace_command_output_max_bytes() -> int:
    return int(getattr(settings, "NEXUS_AGENT_WORKSPACE_COMMAND_OUTPUT_MAX_BYTES", 65_536))


def normalize_workspace_command_timeout(value: int | None) -> int:
    default = int(getattr(settings, "NEXUS_AGENT_WORKSPACE_COMMAND_TIMEOUT_SECONDS", 30))
    maximum = int(getattr(settings, "NEXUS_AGENT_WORKSPACE_COMMAND_TIMEOUT_MAX_SECONDS", 120))
    try:
        requested = default if value is None or value == "" else int(value)
    except (TypeError, ValueError) as exc:
        raise WorkspaceError("Workspace command timeout must be an integer.") from exc
    if requested < 1:
        raise WorkspaceError("Workspace command timeout must be at least 1 second.")
    return min(requested, maximum)


def terminal_tool_status(*, runner: str, tools: dict[str, dict[str, Any]], error: str = "") -> dict[str, Any]:
    return {
        "checked_at": timezone.now().isoformat(),
        "runner": runner,
        "tools": tools,
        "error": error,
    }


def terminal_tool_status_error(*, error: str, runner: str) -> dict[str, Any]:
    return terminal_tool_status(
        runner=runner,
        tools={
            "codex": {"installed": False, "command": "codex", "path": "", "version": ""},
            "claude_code": {"installed": False, "command": "claude", "path": "", "version": ""},
        },
        error=redact_workspace_output(error),
    )


def redact_workspace_output(text: str, secrets: list[str] | None = None) -> str:
    redacted = str(text or "")
    for secret in secrets or []:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted[:1024] or "Workspace command failed."
