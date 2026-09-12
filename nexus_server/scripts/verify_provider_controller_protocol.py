"""Standalone smoke test for the real Provider Controller socket implementation.

This deliberately stubs only the Django/model boundary so release images can
exercise the actual bounded socket server and client without a database.
"""
from __future__ import annotations

import json
import os
import socket
import stat
import sys
import tempfile
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path


SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))


settings = types.SimpleNamespace(
    NEXUS_PROVIDER_RUNTIME_CONTROLLER_SOCKET="",
    NEXUS_PROVIDER_RUNTIME_CONTROLLER_TOKEN="",
    NEXUS_PROVIDER_RUNTIME_CONTROLLER_TIMEOUT_SECONDS=5,
    NEXUS_PROVIDER_RUNTIME_CONTROLLER_WORKERS=2,
)
django_conf = types.ModuleType("django.conf")
django_conf.settings = settings
django_db = types.ModuleType("django.db")
django_db.close_old_connections = lambda: None
sys.modules["django.conf"] = django_conf
sys.modules["django.db"] = django_db

crypto = types.ModuleType("apps.common.crypto")
crypto.decrypt_secret = lambda value: value
sys.modules["apps.common.crypto"] = crypto
models = types.ModuleType("apps.providers.models")
models.ProviderRuntimeAccount = type("ProviderRuntimeAccount", (), {})
sys.modules["apps.providers.models"] = models


class BaseProviderRuntimeRunner:
    pass


class DockerProviderRuntimeRunner:
    pass


@dataclass(frozen=True)
class ProviderRuntimeStartResult:
    container_id: str
    internal_login_url: str
    internal_api_url: str


@dataclass(frozen=True)
class ProviderRuntimeHealthResult:
    healthy: bool
    login_required: bool
    reason: str


@dataclass(frozen=True)
class ProviderRuntimeLoginResult:
    success: bool
    login_required: bool
    reason: str


runner = types.ModuleType("apps.providers.runtime_runner")
runner.BaseProviderRuntimeRunner = BaseProviderRuntimeRunner
runner.DockerProviderRuntimeRunner = DockerProviderRuntimeRunner
runner.ProviderRuntimeStartResult = ProviderRuntimeStartResult
runner.ProviderRuntimeHealthResult = ProviderRuntimeHealthResult
runner.ProviderRuntimeLoginResult = ProviderRuntimeLoginResult
sys.modules["apps.providers.runtime_runner"] = runner

from apps.providers.provider_controller import (  # noqa: E402
    ProviderRuntimeControllerClient,
    ProviderRuntimeControllerError,
    ProviderRuntimeControllerServer,
)


def main() -> None:
    barrier = threading.Barrier(2)

    def dispatch(request):
        if request["action"] == "ping":
            return {"status": "ready", "protocol_version": 1}
        barrier.wait(timeout=3)
        return {"runtime_id": request["runtime_id"]}

    with tempfile.TemporaryDirectory() as tempdir:
        if hasattr(socket, "AF_UNIX"):
            address = str(Path(tempdir) / "controller.sock")
            transport = "unix"
        else:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", 0))
                address = f"tcp://127.0.0.1:{listener.getsockname()[1]}"
            transport = "loopback-test"
        server = ProviderRuntimeControllerServer(
            socket_path=address,
            token="smoke-token",
            dispatcher=dispatch,
        )
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        client = ProviderRuntimeControllerClient(socket_path=address, token="smoke-token")
        deadline = time.monotonic() + 5
        while True:
            try:
                client.ping()
                break
            except ProviderRuntimeControllerError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)

        if transport == "unix":
            mode = stat.S_IMODE(os.stat(address).st_mode)
            if mode != 0o666:
                raise AssertionError(f"unexpected socket mode: {oct(mode)}")
        wrong = ProviderRuntimeControllerClient(socket_path=address, token="wrong-token")
        try:
            wrong.ping()
        except ProviderRuntimeControllerError:
            pass
        else:
            raise AssertionError("invalid controller token was accepted")
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(client.request, action="start", runtime_id=f"runtime-{index}")
                for index in range(2)
            ]
            results = [future.result(timeout=5) for future in futures]
        if {row["runtime_id"] for row in results} != {"runtime-0", "runtime-1"}:
            raise AssertionError("concurrent controller requests crossed results")
        server.stop()
        server_thread.join(timeout=3)
        if server_thread.is_alive():
            raise AssertionError("controller did not stop")
    print(json.dumps({"ok": True, "transport": transport, "concurrent_requests": 2}))


if __name__ == "__main__":
    main()
