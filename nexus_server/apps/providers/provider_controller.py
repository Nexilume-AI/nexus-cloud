from __future__ import annotations

import hmac
import json
import os
import shutil
import socket
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from django.conf import settings
from django.db import close_old_connections

from apps.common.crypto import decrypt_secret

from .models import ProviderRuntimeAccount
from .runtime_runner import (
    BaseProviderRuntimeRunner,
    DockerProviderRuntimeRunner,
    ProviderRuntimeHealthResult,
    ProviderRuntimeLoginResult,
    ProviderRuntimeStartResult,
)


PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 64 * 1024
ALLOWED_ACTIONS = {"ping", "execution_setup", "start", "restore", "stop", "health", "login_with_credentials"}


class ProviderRuntimeControllerError(RuntimeError):
    pass


class ProviderRuntimeControllerClient:
    def __init__(self, *, socket_path: str | None = None, token: str | None = None) -> None:
        self.socket_path = socket_path or str(settings.NEXUS_PROVIDER_RUNTIME_CONTROLLER_SOCKET)
        self.token = token if token is not None else str(settings.NEXUS_PROVIDER_RUNTIME_CONTROLLER_TOKEN)

    def request(
        self,
        *,
        action: str,
        runtime_id: str = "",
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
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
        if len(frame) > MAX_FRAME_BYTES:
            raise ProviderRuntimeControllerError("Provider Runtime Controller request is too large.")
        timeout = timeout if timeout is not None else float(getattr(settings, "NEXUS_PROVIDER_RUNTIME_CONTROLLER_TIMEOUT_SECONDS", 1900))
        try:
            family, address = _socket_address(self.socket_path)
            with socket.socket(family, socket.SOCK_STREAM) as client:
                client.settimeout(timeout)
                client.connect(address)
                client.sendall(frame)
                response = _read_frame(client)
        except (OSError, TimeoutError) as exc:
            raise ProviderRuntimeControllerError(
                "Provider Runtime Controller is unavailable."
            ) from exc
        if not response.get("ok"):
            raise ProviderRuntimeControllerError(
                str(response.get("message") or "Provider Runtime Controller rejected the operation.")
            )
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    def ping(self) -> dict[str, Any]:
        return self.request(action="ping")


class ControllerProviderRuntimeRunner(BaseProviderRuntimeRunner):
    def __init__(self, *, client: ProviderRuntimeControllerClient | None = None) -> None:
        self.client = client or ProviderRuntimeControllerClient()

    def start(self, *, runtime: ProviderRuntimeAccount, proxy_api_key: str) -> ProviderRuntimeStartResult:
        result = self.client.request(action="start", runtime_id=str(runtime.id))
        return ProviderRuntimeStartResult(**result)

    def restore(self, *, runtime: ProviderRuntimeAccount, proxy_api_key: str) -> ProviderRuntimeStartResult:
        result = self.client.request(action="restore", runtime_id=str(runtime.id))
        health = result.pop("health", None)
        return ProviderRuntimeStartResult(**result, health=ProviderRuntimeHealthResult(**health) if health else None)

    def stop(self, *, runtime: ProviderRuntimeAccount) -> None:
        self.client.request(action="stop", runtime_id=str(runtime.id))

    def health_check(self, *, runtime: ProviderRuntimeAccount) -> ProviderRuntimeHealthResult:
        result = self.client.request(action="health", runtime_id=str(runtime.id))
        return ProviderRuntimeHealthResult(**result)

    def login_with_credentials(
        self,
        *,
        runtime: ProviderRuntimeAccount,
        username: str,
        password: str,
    ) -> ProviderRuntimeLoginResult:
        result = self.client.request(
            action="login_with_credentials",
            runtime_id=str(runtime.id),
            payload={"username": username, "password": password},
        )
        return ProviderRuntimeLoginResult(**result)


class ProviderRuntimeControllerServer:
    def __init__(
        self,
        *,
        socket_path: str | None = None,
        token: str | None = None,
        dispatcher: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.socket_path = socket_path or str(settings.NEXUS_PROVIDER_RUNTIME_CONTROLLER_SOCKET)
        self.token = token if token is not None else str(settings.NEXUS_PROVIDER_RUNTIME_CONTROLLER_TOKEN)
        self.dispatcher = dispatcher or dispatch_provider_runtime_command
        self._stopping = threading.Event()
        self._server: socket.socket | None = None

    def serve_forever(self) -> None:
        if not self.token:
            raise ProviderRuntimeControllerError("Provider Runtime Controller token is not configured.")
        family, address = _socket_address(self.socket_path)
        unix_path = Path(self.socket_path) if family == getattr(socket, "AF_UNIX", None) else None
        if unix_path is not None:
            unix_path.parent.mkdir(parents=True, exist_ok=True)
            # The socket volume is mounted only into trusted Nexus processes.
            # The per-request token remains mandatory; these modes merely let
            # a non-root Web/Worker UID traverse a root-owned controller volume.
            os.chmod(unix_path.parent, 0o755)
            unix_path.unlink(missing_ok=True)
        worker_count = int(getattr(settings, "NEXUS_PROVIDER_RUNTIME_CONTROLLER_WORKERS", 8))
        if not 1 <= worker_count <= 64:
            raise ProviderRuntimeControllerError("Provider Runtime Controller workers must be between 1 and 64.")
        server = socket.socket(family, socket.SOCK_STREAM)
        self._server = server
        try:
            # Reuse only closed POSIX TCP endpoints, never a live listener.
            if os.name == "posix" and family in (socket.AF_INET, socket.AF_INET6):
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(address)
            if unix_path is not None:
                os.chmod(unix_path, 0o666)
            server.listen(16)
            server.settimeout(1.0)
        except BaseException:
            server.close()
            self._server = None
            raise
        capacity = threading.BoundedSemaphore(worker_count * 2)
        try:
            with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="provider-runtime") as executor:
                while not self._stopping.is_set():
                    try:
                        connection, _ = server.accept()
                    except socket.timeout:
                        continue
                    if not capacity.acquire(blocking=False):
                        try:
                            connection.sendall(
                                b'{"ok":false,"message":"Provider Runtime Controller is busy."}\n'
                            )
                        except OSError:
                            pass
                        connection.close()
                        continue
                    executor.submit(self._serve_connection, connection, capacity)
        finally:
            server.close()
            self._server = None
            if unix_path is not None:
                unix_path.unlink(missing_ok=True)

    def stop(self) -> None:
        self._stopping.set()

    def _handle(self, connection: socket.socket) -> dict[str, Any]:
        try:
            request = _read_frame(connection)
            if request.get("version") != PROTOCOL_VERSION:
                raise ProviderRuntimeControllerError("Unsupported Provider Runtime Controller protocol version.")
            supplied_token = str(request.get("token") or "")
            if not hmac.compare_digest(supplied_token, self.token):
                raise ProviderRuntimeControllerError("Provider Runtime Controller authentication failed.")
            action = str(request.get("action") or "")
            if action not in ALLOWED_ACTIONS:
                raise ProviderRuntimeControllerError("Unsupported Provider Runtime Controller action.")
            return {"ok": True, "request_id": request.get("request_id"), "result": self.dispatcher(request)}
        except Exception as exc:
            return {"ok": False, "message": _safe_error_message(exc)}

    def _serve_connection(self, connection: socket.socket, capacity: threading.BoundedSemaphore) -> None:
        try:
            close_old_connections()
            with connection:
                connection.settimeout(
                    float(getattr(settings, "NEXUS_PROVIDER_RUNTIME_CONTROLLER_TIMEOUT_SECONDS", 1900))
                )
                response = self._handle(connection)
                try:
                    connection.sendall(
                        json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n"
                    )
                except OSError:
                    pass
        finally:
            close_old_connections()
            capacity.release()


def dispatch_provider_runtime_command(request: dict[str, Any]) -> dict[str, Any]:
    action = str(request["action"])
    if action == "ping":
        return {"status": "ready", "protocol_version": PROTOCOL_VERSION}
    if action == "execution_setup":
        from .execution_setup import inspect_execution
        return {"engines": inspect_execution()}
    runtime_id = str(request.get("runtime_id") or "")
    if not runtime_id:
        raise ProviderRuntimeControllerError("Provider Runtime ID is required.")
    try:
        runtime = ProviderRuntimeAccount.objects.select_related("source_provider_account").get(id=runtime_id)
    except (ProviderRuntimeAccount.DoesNotExist, ValueError) as exc:
        raise ProviderRuntimeControllerError("Provider Runtime was not found.") from exc

    runner = DockerProviderRuntimeRunner()
    if action in {"start", "restore"}:
        if not runtime.encrypted_proxy_api_key:
            raise ProviderRuntimeControllerError("Provider Runtime credential is unavailable.")
        proxy_api_key = decrypt_secret(runtime.encrypted_proxy_api_key)
        operation = runner.start if action == "start" else runner.restore
        result = operation(runtime=runtime, proxy_api_key=proxy_api_key)
        response = {
            "container_id": result.container_id,
            "internal_login_url": result.internal_login_url,
            "internal_api_url": result.internal_api_url,
        }
        if action == "restore":
            # The caller hasn't committed the recovered endpoint yet. Probe
            # the endpoint we just restored, not the old database snapshot.
            runtime.container_id = result.container_id
            runtime.internal_login_url = result.internal_login_url
            runtime.internal_api_url = result.internal_api_url
            health = runner.health_check(runtime=runtime)
            response["health"] = {
                "healthy": health.healthy, "login_required": health.login_required,
                "reason": health.reason[:1024], "recovery_recommended": health.recovery_recommended,
                "inconclusive": getattr(health, "inconclusive", False) is True,
            }
        return response
    if action == "stop":
        runner.stop(runtime=runtime)
        return {}
    if action == "health":
        result = runner.health_check(runtime=runtime)
        return {
            "healthy": result.healthy,
            "login_required": result.login_required,
            "reason": result.reason[:1024],
            "recovery_recommended": result.recovery_recommended,
            "inconclusive": getattr(result, "inconclusive", False) is True,
        }
    payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
    username = str(payload.get("username") or "")
    password = str(payload.get("password") or "")
    result = runner.login_with_credentials(runtime=runtime, username=username, password=password)
    return {
        "success": result.success,
        "login_required": result.login_required,
        "reason": result.reason[:1024],
    }


def provider_runtime_controller_readiness() -> dict[str, Any]:
    if not getattr(settings, "NEXUS_PRODUCTION", False):
        return {"ok": True, "required": False}
    if str(getattr(settings, "NEXUS_PROVIDER_RUNTIME_RUNNER", "")).lower() != "controller":
        return {"ok": False, "code": "PROVIDER_RUNTIME_CONTROLLER_REQUIRED"}
    try:
        result = ProviderRuntimeControllerClient().ping()
    except ProviderRuntimeControllerError:
        return {"ok": False, "code": "PROVIDER_RUNTIME_CONTROLLER_UNAVAILABLE"}
    if result.get("status") != "ready":
        return {"ok": False, "code": "PROVIDER_RUNTIME_CONTROLLER_NOT_READY"}
    return {"ok": True, "protocol_version": result.get("protocol_version")}


def ensure_provider_runtime_controller_dependencies() -> None:
    executable = shutil.which("docker")
    if not executable:
        raise ProviderRuntimeControllerError("Docker CLI is unavailable in the Provider Runtime Controller image.")
    try:
        result = subprocess.run(
            [executable, "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProviderRuntimeControllerError("Docker Engine is unavailable to the Provider Runtime Controller.") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise ProviderRuntimeControllerError("Docker Engine is unavailable to the Provider Runtime Controller.")


def _read_frame(connection: socket.socket) -> dict[str, Any]:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = connection.recv(min(4096, MAX_FRAME_BYTES + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
        if size > MAX_FRAME_BYTES:
            raise ProviderRuntimeControllerError("Provider Runtime Controller frame is too large.")
        if b"\n" in chunk:
            break
    raw = b"".join(chunks)
    if b"\n" not in raw:
        raise ProviderRuntimeControllerError("Incomplete Provider Runtime Controller frame.")
    raw = raw.split(b"\n", 1)[0]
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderRuntimeControllerError("Invalid Provider Runtime Controller frame.") from exc
    if not isinstance(value, dict):
        raise ProviderRuntimeControllerError("Provider Runtime Controller frame must be an object.")
    return value


def _safe_error_message(exc: Exception) -> str:
    if isinstance(exc, ProviderRuntimeControllerError):
        return str(exc)[:512]
    return f"Provider Runtime operation failed ({type(exc).__name__})."


def _socket_address(value: str):
    if value.startswith("tcp://"):
        parsed = urlparse(value)
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or not parsed.port:
            raise ProviderRuntimeControllerError("Controller TCP transport must use an explicit loopback port.")
        family = socket.AF_INET6 if parsed.hostname == "::1" else socket.AF_INET
        return family, (parsed.hostname, parsed.port)
    if not hasattr(socket, "AF_UNIX"):
        raise ProviderRuntimeControllerError("Unix sockets are unavailable on this platform.")
    return socket.AF_UNIX, value
