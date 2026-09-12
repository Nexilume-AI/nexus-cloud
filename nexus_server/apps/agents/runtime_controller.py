from __future__ import annotations

import base64
import hmac
import json
import os
import shutil
import socket
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlparse

from django.conf import settings
from django.db import close_old_connections

from .models import AgentRuntimeDeployment
from .runtime_runner import (
    BaseAgentRuntimeRunner,
    DockerAgentRuntimeRunner,
    RuntimeDisplayContext,
    RuntimeMCPResult,
    RuntimeMCPStreamResult,
    RuntimeStartResult,
    RuntimeWorkspaceContext,
)


PROTOCOL_VERSION = 1
DEFAULT_MAX_FRAME_BYTES = 16 * 1024 * 1024
STREAM_CHUNK_BYTES = 48 * 1024
ALLOWED_ACTIONS = {
    "ping",
    "resolve_image",
    "validate_image",
    "load_image",
    "start",
    "stop",
    "health",
    "call_mcp",
    "stream_mcp",
    "diagnostics",
    "sweep_orphans",
}


class AgentRuntimeControllerError(RuntimeError):
    pass


def _max_frame_bytes() -> int:
    return int(getattr(settings, "NEXUS_AGENT_RUNTIME_CONTROLLER_MAX_FRAME_BYTES", DEFAULT_MAX_FRAME_BYTES))


class AgentRuntimeControllerClient:
    def __init__(self, *, socket_path: str | None = None, token: str | None = None) -> None:
        self.socket_path = socket_path or str(settings.NEXUS_AGENT_RUNTIME_CONTROLLER_SOCKET)
        self.token = token if token is not None else str(settings.NEXUS_AGENT_RUNTIME_CONTROLLER_TOKEN)

    def _frame(self, *, action: str, runtime_id: str = "", payload: dict[str, Any] | None = None) -> bytes:
        frame = json.dumps(
            {
                "version": PROTOCOL_VERSION,
                "request_id": uuid.uuid4().hex,
                "token": self.token,
                "action": action,
                "runtime_id": runtime_id,
                "payload": payload or {},
            },
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        if len(frame) > _max_frame_bytes():
            raise AgentRuntimeControllerError("Agent Runtime Controller request is too large.")
        return frame

    def _connect(self) -> socket.socket:
        family, address = _socket_address(self.socket_path)
        client = socket.socket(family, socket.SOCK_STREAM)
        client.settimeout(float(getattr(settings, "NEXUS_AGENT_RUNTIME_CONTROLLER_TIMEOUT_SECONDS", 1900)))
        try:
            client.connect(address)
        except (OSError, TimeoutError) as exc:
            client.close()
            raise AgentRuntimeControllerError("Agent Runtime Controller is unavailable.") from exc
        return client

    def request(
        self,
        *,
        action: str,
        runtime_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        frame = self._frame(action=action, runtime_id=runtime_id, payload=payload)
        client = self._connect()
        try:
            client.sendall(frame)
            with client.makefile("rb") as stream:
                response = _read_frame(stream)
        except (OSError, TimeoutError) as exc:
            raise AgentRuntimeControllerError("Agent Runtime Controller is unavailable.") from exc
        finally:
            client.close()
        if not response.get("ok"):
            error = AgentRuntimeControllerError(
                str(response.get("message") or "Agent Runtime Controller rejected the operation.")
            )
            error.runtime_diagnostics = response.get("diagnostics") if isinstance(response.get("diagnostics"), dict) else {}
            raise error
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    def stream(
        self,
        *,
        runtime_id: str,
        payload: dict[str, Any],
    ) -> RuntimeMCPStreamResult:
        frame = self._frame(action="stream_mcp", runtime_id=runtime_id, payload=payload)
        client = self._connect()
        try:
            client.sendall(frame)
            stream = client.makefile("rb")
            first = _read_frame(stream)
            if not first.get("ok"):
                stream.close()
                client.close()
                raise AgentRuntimeControllerError(
                    str(first.get("message") or "Agent Runtime Controller rejected the stream.")
                )
        except Exception:
            client.close()
            raise

        def chunks() -> Iterator[bytes]:
            try:
                while True:
                    frame = _read_frame(stream)
                    if frame.get("eof"):
                        return
                    if not frame.get("ok", True):
                        raise AgentRuntimeControllerError(
                            str(frame.get("message") or "Agent Runtime stream failed.")
                        )
                    encoded = frame.get("chunk")
                    if not isinstance(encoded, str):
                        raise AgentRuntimeControllerError("Agent Runtime Controller returned an invalid stream frame.")
                    yield base64.b64decode(encoded, validate=True)
            finally:
                stream.close()
                client.close()

        return RuntimeMCPStreamResult(
            status_code=int(first.get("status_code") or 502),
            headers={str(k): str(v) for k, v in (first.get("headers") or {}).items()},
            chunks=chunks(),
        )

    def ping(self) -> dict[str, Any]:
        return self.request(action="ping")


class ControllerAgentRuntimeRunner(BaseAgentRuntimeRunner):
    def __init__(self, *, client: AgentRuntimeControllerClient | None = None) -> None:
        self.client = client or AgentRuntimeControllerClient()

    def resolve_image(self, *, image_ref: str) -> str:
        return str(self.client.request(action="resolve_image", payload={"image_ref": image_ref}).get("digest") or "")

    def validate_image_ref(self, *, image_ref: str) -> None:
        self.client.request(action="validate_image", payload={"image_ref": image_ref})

    def load_image(self, *, artifact_path: str, image_ref: str = "") -> str:
        result = self.client.request(
            action="load_image",
            payload={"artifact_path": artifact_path, "image_ref": image_ref},
        )
        return str(result.get("image_ref") or "")

    def start(
        self,
        *,
        deployment: AgentRuntimeDeployment,
        display_context: RuntimeDisplayContext | None = None,
        workspace_context: RuntimeWorkspaceContext | None = None,
    ) -> RuntimeStartResult:
        result = self.client.request(
            action="start",
            runtime_id=str(deployment.id),
            payload={
                "display_context": asdict(display_context) if display_context else None,
                "workspace_context": asdict(workspace_context) if workspace_context else None,
            },
        )
        return RuntimeStartResult(
            container_id=str(result.get("container_id") or ""),
            internal_mcp_url=str(result.get("internal_mcp_url") or ""),
        )

    def stop(self, *, deployment: AgentRuntimeDeployment) -> None:
        self.client.request(action="stop", runtime_id=str(deployment.id), payload={"container_id": deployment.container_id})

    def health_check(self, *, deployment: AgentRuntimeDeployment) -> bool:
        return bool(self.client.request(action="health", runtime_id=str(deployment.id)).get("healthy"))

    def diagnostics(self, *, deployment: AgentRuntimeDeployment) -> dict[str, Any]:
        return self.client.request(action="diagnostics", runtime_id=str(deployment.id))

    def sweep_orphans(self, *, valid_runtime_ids: set[str]) -> dict[str, int]:
        result = self.client.request(action="sweep_orphans", payload={"valid_runtime_ids": sorted(valid_runtime_ids)})
        return {"containers": int(result.get("containers") or 0), "networks": int(result.get("networks") or 0)}

    @staticmethod
    def _mcp_payload(*, method: str, headers: dict[str, str], body: bytes, path: str, timeout=None) -> dict[str, Any]:
        return {
            "method": method,
            "headers": headers,
            "body": base64.b64encode(body).decode("ascii"),
            "path": path,
            "timeout": timeout,
        }

    def call_mcp(self, *, deployment, method, headers, body, path="", timeout=None) -> RuntimeMCPResult:
        result = self.client.request(
            action="call_mcp",
            runtime_id=str(deployment.id),
            payload=self._mcp_payload(method=method, headers=headers, body=body, path=path, timeout=timeout),
        )
        try:
            response_body = base64.b64decode(str(result.get("body") or ""), validate=True)
        except ValueError as exc:
            raise AgentRuntimeControllerError("Agent Runtime Controller returned an invalid response.") from exc
        return RuntimeMCPResult(
            status_code=int(result.get("status_code") or 502),
            headers={str(k): str(v) for k, v in (result.get("headers") or {}).items()},
            body=response_body,
        )

    def stream_mcp(self, *, deployment, method, headers, body, path="") -> RuntimeMCPStreamResult:
        return self.client.stream(
            runtime_id=str(deployment.id),
            payload=self._mcp_payload(method=method, headers=headers, body=body, path=path),
        )


class AgentRuntimeControllerServer:
    def __init__(
        self,
        *,
        socket_path: str | None = None,
        token: str | None = None,
        dispatcher: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        stream_dispatcher: Callable[[dict[str, Any]], RuntimeMCPStreamResult] | None = None,
    ) -> None:
        self.socket_path = socket_path or str(settings.NEXUS_AGENT_RUNTIME_CONTROLLER_SOCKET)
        self.token = token if token is not None else str(settings.NEXUS_AGENT_RUNTIME_CONTROLLER_TOKEN)
        self.dispatcher = dispatcher or dispatch_agent_runtime_command
        self.stream_dispatcher = stream_dispatcher or dispatch_agent_runtime_stream
        self._stopping = threading.Event()

    def serve_forever(self) -> None:
        if not self.token:
            raise AgentRuntimeControllerError("Agent Runtime Controller token is not configured.")
        family, address = _socket_address(self.socket_path)
        unix_path = Path(self.socket_path) if family == getattr(socket, "AF_UNIX", None) else None
        if unix_path is not None:
            unix_path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(unix_path.parent, 0o755)
            unix_path.unlink(missing_ok=True)
        workers = int(getattr(settings, "NEXUS_AGENT_RUNTIME_CONTROLLER_WORKERS", 8))
        if not 1 <= workers <= 64:
            raise AgentRuntimeControllerError("Agent Runtime Controller workers must be between 1 and 64.")
        server = socket.socket(family, socket.SOCK_STREAM)
        try:
            # POSIX TCP restart must tolerate this listener's TIME_WAIT sockets.
            # Do not enable SO_REUSEPORT or Windows' endpoint-sharing semantics.
            if os.name == "posix" and family in (socket.AF_INET, socket.AF_INET6):
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(address)
            if unix_path is not None:
                os.chmod(unix_path, 0o666)
            server.listen(32)
            server.settimeout(1.0)
        except BaseException:
            server.close()
            raise
        capacity = threading.BoundedSemaphore(workers * 2)
        try:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="agent-runtime") as executor:
                while not self._stopping.is_set():
                    try:
                        connection, _ = server.accept()
                    except socket.timeout:
                        continue
                    if not capacity.acquire(blocking=False):
                        _send_frame(connection, {"ok": False, "message": "Agent Runtime Controller is busy."})
                        connection.close()
                        continue
                    executor.submit(self._serve_connection, connection, capacity)
        finally:
            server.close()
            if unix_path is not None:
                unix_path.unlink(missing_ok=True)

    def stop(self) -> None:
        self._stopping.set()

    def _authenticate(self, request: dict[str, Any]) -> None:
        if request.get("version") != PROTOCOL_VERSION:
            raise AgentRuntimeControllerError("Unsupported Agent Runtime Controller protocol version.")
        if not hmac.compare_digest(str(request.get("token") or ""), self.token):
            raise AgentRuntimeControllerError("Agent Runtime Controller authentication failed.")
        if str(request.get("action") or "") not in ALLOWED_ACTIONS:
            raise AgentRuntimeControllerError("Unsupported Agent Runtime Controller action.")

    def _serve_connection(self, connection: socket.socket, capacity: threading.BoundedSemaphore) -> None:
        try:
            close_old_connections()
            with connection:
                connection.settimeout(float(getattr(settings, "NEXUS_AGENT_RUNTIME_CONTROLLER_TIMEOUT_SECONDS", 1900)))
                with connection.makefile("rb") as stream:
                    request = _read_frame(stream)
                try:
                    self._authenticate(request)
                except Exception as exc:
                    _send_frame(connection, _error_frame(exc))
                    return
                if request.get("action") == "stream_mcp":
                    self._serve_stream(connection, request)
                    return
                try:
                    result = self.dispatcher(request)
                    _send_frame(connection, {"ok": True, "request_id": request.get("request_id"), "result": result})
                except Exception as exc:
                    _send_frame(connection, _error_frame(exc))
        except Exception as exc:
            try:
                _send_frame(connection, _error_frame(exc))
            except OSError:
                pass
        finally:
            close_old_connections()
            capacity.release()

    def _serve_stream(self, connection: socket.socket, request: dict[str, Any]) -> None:
        result = self.stream_dispatcher(request)
        _send_frame(
            connection,
            {"ok": True, "status_code": result.status_code, "headers": result.headers, "stream": True},
        )
        try:
            for chunk in result.chunks:
                for offset in range(0, len(chunk), STREAM_CHUNK_BYTES):
                    _send_frame(
                        connection,
                        {"ok": True, "chunk": base64.b64encode(chunk[offset:offset + STREAM_CHUNK_BYTES]).decode("ascii")},
                    )
            _send_frame(connection, {"ok": True, "eof": True})
        except Exception as exc:
            _send_frame(connection, {"ok": False, "message": _safe_error_message(exc)})
        finally:
            close = getattr(result.chunks, "close", None)
            if callable(close):
                close()


def _runtime(runtime_id: str) -> AgentRuntimeDeployment:
    if not runtime_id:
        raise AgentRuntimeControllerError("Agent Runtime ID is required.")
    try:
        return AgentRuntimeDeployment.objects.select_related(
            "tenant", "agent", "image", "image__version", "project", "agent_deployment"
        ).get(id=runtime_id, runtime_kind=AgentRuntimeDeployment.RUNTIME_DOCKER)
    except (AgentRuntimeDeployment.DoesNotExist, ValueError) as exc:
        raise AgentRuntimeControllerError("Agent Runtime was not found.") from exc


def _decode_mcp_payload(payload: dict[str, Any]) -> tuple[str, dict[str, str], bytes, str, Any]:
    encoded = payload.get("body")
    if not isinstance(encoded, str):
        raise AgentRuntimeControllerError("Agent Runtime request body is invalid.")
    try:
        body = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise AgentRuntimeControllerError("Agent Runtime request body is invalid.") from exc
    headers = payload.get("headers") if isinstance(payload.get("headers"), dict) else {}
    return (
        str(payload.get("method") or "POST"),
        {str(k): str(v) for k, v in headers.items()},
        body,
        str(payload.get("path") or ""),
        payload.get("timeout"),
    )


def dispatch_agent_runtime_command(request: dict[str, Any]) -> dict[str, Any]:
    action = str(request["action"])
    payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
    runner = DockerAgentRuntimeRunner()
    if action == "ping":
        return {"status": "ready", "protocol_version": PROTOCOL_VERSION}
    if action == "resolve_image":
        return {"digest": runner.resolve_image(image_ref=str(payload.get("image_ref") or ""))}
    if action == "validate_image":
        runner.validate_image_ref(image_ref=str(payload.get("image_ref") or ""))
        return {}
    if action == "load_image":
        artifact_path = _shared_artifact_path(str(payload.get("artifact_path") or ""))
        return {"image_ref": runner.load_image(artifact_path=artifact_path, image_ref=str(payload.get("image_ref") or ""))}
    if action == "sweep_orphans":
        raw_ids = payload.get("valid_runtime_ids") if isinstance(payload.get("valid_runtime_ids"), list) else []
        return runner.sweep_orphans(valid_runtime_ids={str(value) for value in raw_ids})
    runtime = _runtime(str(request.get("runtime_id") or ""))
    if action == "start":
        display = payload.get("display_context")
        workspace = payload.get("workspace_context")
        result = runner.start(
            deployment=runtime,
            display_context=RuntimeDisplayContext(**display) if isinstance(display, dict) else None,
            workspace_context=RuntimeWorkspaceContext(**workspace) if isinstance(workspace, dict) else None,
        )
        return {"container_id": result.container_id, "internal_mcp_url": result.internal_mcp_url}
    if action == "stop":
        supplied_container = str(payload.get("container_id") or "")
        if supplied_container:
            runtime.container_id = supplied_container
        runner.stop(deployment=runtime)
        return {}
    if action == "health":
        return {"healthy": runner.health_check(deployment=runtime)}
    if action == "diagnostics":
        return runner.diagnostics(deployment=runtime)
    if action == "call_mcp":
        method, headers, body, path, timeout = _decode_mcp_payload(payload)
        result = runner.call_mcp(
            deployment=runtime, method=method, headers=headers, body=body, path=path, timeout=timeout
        )
        return {
            "status_code": result.status_code,
            "headers": result.headers,
            "body": base64.b64encode(result.body).decode("ascii"),
        }
    raise AgentRuntimeControllerError("Unsupported Agent Runtime Controller action.")


def dispatch_agent_runtime_stream(request: dict[str, Any]) -> RuntimeMCPStreamResult:
    runtime = _runtime(str(request.get("runtime_id") or ""))
    payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
    method, headers, body, path, _timeout = _decode_mcp_payload(payload)
    return DockerAgentRuntimeRunner().stream_mcp(
        deployment=runtime, method=method, headers=headers, body=body, path=path
    )


def agent_runtime_controller_readiness() -> dict[str, Any]:
    if not getattr(settings, "NEXUS_PRODUCTION", False):
        return {"ok": True, "required": False}
    if str(getattr(settings, "NEXUS_AGENT_RUNTIME_RUNNER", "")).lower() != "controller":
        return {"ok": False, "code": "AGENT_RUNTIME_CONTROLLER_REQUIRED"}
    try:
        result = AgentRuntimeControllerClient().ping()
    except AgentRuntimeControllerError:
        return {"ok": False, "code": "AGENT_RUNTIME_CONTROLLER_UNAVAILABLE"}
    if result.get("status") != "ready":
        return {"ok": False, "code": "AGENT_RUNTIME_CONTROLLER_NOT_READY"}
    return {"ok": True, "protocol_version": result.get("protocol_version")}


def ensure_agent_runtime_controller_dependencies() -> None:
    executable = shutil.which("docker")
    if not executable:
        raise AgentRuntimeControllerError("Docker CLI is unavailable in the Agent Runtime Controller image.")
    try:
        result = subprocess.run(
            [executable, "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AgentRuntimeControllerError("Docker Engine is unavailable to the Agent Runtime Controller.") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise AgentRuntimeControllerError("Docker Engine is unavailable to the Agent Runtime Controller.")
    admission = getattr(settings, "NEXUS_AGENT_IMAGE_ADMISSION_COMMAND", [])
    if getattr(settings, "NEXUS_PRODUCTION", False):
        if not isinstance(admission, list) or not admission or not all(
            isinstance(value, str) and value for value in admission
        ):
            raise AgentRuntimeControllerError("Agent image admission verification is not configured.")
        executable = admission[0]
        if not shutil.which(executable) and not Path(executable).is_file():
            raise AgentRuntimeControllerError("Agent image admission verifier is unavailable.")


def _shared_artifact_path(value: str) -> str:
    root = Path(settings.NEXUS_SHARED_STORAGE_ROOT).resolve()
    candidate = Path(value).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise AgentRuntimeControllerError("Agent image artifact is outside shared storage.") from exc
    if not candidate.is_file():
        raise AgentRuntimeControllerError("Agent image artifact was not found.")
    return str(candidate)


def _read_frame(stream) -> dict[str, Any]:
    raw = stream.readline(_max_frame_bytes() + 1)
    if not raw or not raw.endswith(b"\n") or len(raw) > _max_frame_bytes():
        raise AgentRuntimeControllerError("Invalid or oversized Agent Runtime Controller frame.")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AgentRuntimeControllerError("Invalid Agent Runtime Controller frame.") from exc
    if not isinstance(value, dict):
        raise AgentRuntimeControllerError("Agent Runtime Controller frame must be an object.")
    return value


def _send_frame(connection: socket.socket, value: dict[str, Any]) -> None:
    encoded = json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(encoded) > _max_frame_bytes():
        raise AgentRuntimeControllerError("Agent Runtime Controller response is too large.")
    connection.sendall(encoded)


def _safe_error_message(exc: Exception) -> str:
    if isinstance(exc, AgentRuntimeControllerError):
        return str(exc)[:512]
    detail = str(getattr(exc, "detail", "") or "")
    allowed_codes = (
        "AGENT_EGRESS_POLICY_REQUIRED",
        "AGENT_IMAGE_ADMISSION_REQUIRED",
        "AGENT_IMAGE_ADMISSION_REJECTED",
        "AGENT_IMAGE_ADMISSION_UNAVAILABLE",
        "AGENT_DOCKER_HOST_REQUIRED",
        "AGENT_DOCKER_HOST_MISMATCH",
        "AGENT_DOCKER_TOPOLOGY_UNSUPPORTED",
    )
    for code in allowed_codes:
        if code in detail:
            return code
    return f"Agent Runtime operation failed ({type(exc).__name__})."


def _error_frame(exc: Exception) -> dict[str, Any]:
    frame: dict[str, Any] = {"ok": False, "message": _safe_error_message(exc)}
    diagnostics = getattr(exc, "runtime_diagnostics", None)
    if isinstance(diagnostics, dict):
        frame["diagnostics"] = diagnostics
    return frame


def _socket_address(value: str):
    if value.startswith("tcp://"):
        parsed = urlparse(value)
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or not parsed.port:
            raise AgentRuntimeControllerError("Controller TCP transport must use an explicit loopback port.")
        family = socket.AF_INET6 if parsed.hostname == "::1" else socket.AF_INET
        return family, (parsed.hostname, parsed.port)
    if not hasattr(socket, "AF_UNIX"):
        raise AgentRuntimeControllerError("Unix sockets are unavailable on this platform.")
    return socket.AF_UNIX, value
