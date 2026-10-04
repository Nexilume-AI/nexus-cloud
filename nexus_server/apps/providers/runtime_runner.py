from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from copy import copy
from dataclasses import dataclass
from http.client import RemoteDisconnected
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener, urlopen

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from rest_framework import exceptions

from apps.common.crypto import decrypt_secret

from .models import ProviderRuntimeAccount
from .runtime_config import merge_runtime_config
from .runtime_errors import ProviderRuntimeUnavailable, docker_daemon_transport_unavailable
from .runtime_release import approved_release, verify_existing_container, verify_image


@dataclass(frozen=True)
class ProviderRuntimeStartResult:
    container_id: str
    internal_login_url: str
    internal_api_url: str
    health: ProviderRuntimeHealthResult | None = None


@dataclass(frozen=True)
class ProviderRuntimeHealthResult:
    healthy: bool
    login_required: bool
    reason: str
    recovery_recommended: bool = False
    inconclusive: bool = False


@dataclass(frozen=True)
class ProviderRuntimeLoginResult:
    success: bool
    login_required: bool
    reason: str


class BaseProviderRuntimeRunner:
    def start(self, *, runtime: ProviderRuntimeAccount, proxy_api_key: str) -> ProviderRuntimeStartResult:
        raise NotImplementedError

    def stop(self, *, runtime: ProviderRuntimeAccount) -> None:
        raise NotImplementedError

    def stop_restored_container(self, *, runtime: ProviderRuntimeAccount, container_id: str) -> None:
        if runtime.status not in {ProviderRuntimeAccount.STATUS_STOPPED, "deleted"}:
            raise exceptions.APIException("Restored container cleanup requires a terminal runtime.")
        restored_runtime = copy(runtime)
        restored_runtime.container_id = container_id
        self.stop(runtime=restored_runtime)

    def restore(self, *, runtime: ProviderRuntimeAccount, proxy_api_key: str) -> ProviderRuntimeStartResult:
        return self.start(runtime=runtime, proxy_api_key=proxy_api_key)

    def health_check(self, *, runtime: ProviderRuntimeAccount) -> ProviderRuntimeHealthResult:
        raise NotImplementedError

    def login_with_credentials(self, *, runtime: ProviderRuntimeAccount, username: str, password: str) -> ProviderRuntimeLoginResult:
        raise NotImplementedError


class FakeProviderRuntimeRunner(BaseProviderRuntimeRunner):
    def start(self, *, runtime: ProviderRuntimeAccount, proxy_api_key: str) -> ProviderRuntimeStartResult:
        suffix = uuid.uuid4().hex[:12]
        return ProviderRuntimeStartResult(
            container_id=f"fake_provider_runtime_{suffix}",
            internal_login_url="",
            internal_api_url=f"http://mock.local/provider-runtime/{runtime.id}/v1",
        )

    def stop(self, *, runtime: ProviderRuntimeAccount) -> None:
        return None

    def restore(self, *, runtime: ProviderRuntimeAccount, proxy_api_key: str) -> ProviderRuntimeStartResult:
        return ProviderRuntimeStartResult(
            container_id=runtime.container_id or f"fake_provider_runtime_{uuid.uuid4().hex[:12]}",
            internal_login_url=runtime.internal_login_url,
            internal_api_url=runtime.internal_api_url or f"http://mock.local/provider-runtime/{runtime.id}/v1",
        )

    def health_check(self, *, runtime: ProviderRuntimeAccount) -> ProviderRuntimeHealthResult:
        healthy = runtime.status == ProviderRuntimeAccount.STATUS_ACTIVE
        return ProviderRuntimeHealthResult(healthy=healthy, login_required=False, reason="fake runtime")

    def login_with_credentials(self, *, runtime: ProviderRuntimeAccount, username: str, password: str) -> ProviderRuntimeLoginResult:
        if username and password:
            return ProviderRuntimeLoginResult(success=True, login_required=False, reason="fake credential login succeeded")
        return ProviderRuntimeLoginResult(success=False, login_required=True, reason="username and password are required")


class LoginProviderRuntimeAdapter(Protocol):
    runtime_type: str
    container_port: int

    def image_name(self, *, runtime: ProviderRuntimeAccount) -> str:
        ...

    def source_dir(self) -> Path:
        ...

    def prepare_storage(self, *, runtime: ProviderRuntimeAccount, proxy_api_key: str) -> bool | None:
        ...

    def docker_env(self, *, proxy_api_key: str) -> dict[str, str]:
        ...

    def docker_security_args(self) -> list[str]:
        ...

    def docker_volumes(self, *, runtime: ProviderRuntimeAccount) -> list[tuple[Path, str]]:
        ...

    def login_url(self, *, host_port: int, host: str = "127.0.0.1") -> str:
        ...

    def api_base_url(self, *, host_port: int, host: str = "127.0.0.1") -> str:
        ...

    def health_urls(self, *, host_port: int, host: str = "127.0.0.1") -> list[str]:
        ...

    def browser_login_url(
        self, *, runtime: ProviderRuntimeAccount, host_port: int, host: str = "127.0.0.1"
    ) -> str:
        ...

    def login_with_credentials(self, *, runtime: ProviderRuntimeAccount, username: str, password: str) -> ProviderRuntimeLoginResult:
        ...


class CodexProxyRuntimeAdapter:
    runtime_type = ProviderRuntimeAccount.RUNTIME_CODEX_PROXY
    container_port = 8080

    def image_name(self, *, runtime: ProviderRuntimeAccount) -> str:
        prefix = str(getattr(settings, "NEXUS_CODEX_PROXY_IMAGE_PREFIX", "nexus-codex-proxy"))
        return f"{prefix}:{shared_runtime_image_tag()}"

    def source_dir(self) -> Path:
        return Path(getattr(settings, "NEXUS_CODEX_PROXY_SOURCE_DIR")).resolve()

    def prepare_storage(self, *, runtime: ProviderRuntimeAccount, proxy_api_key: str) -> bool:
        root = runtime_storage_root(runtime=runtime)
        data_dir = root / "data"
        config_dir = root / "config"
        data_dir.mkdir(parents=True, exist_ok=True)
        config_dir.mkdir(parents=True, exist_ok=True)
        return merge_runtime_config(data_dir / "local.yaml", {
            "server": {"host": "0.0.0.0", "port": 8080, "proxy_api_key": proxy_api_key},
        })

    def docker_env(self, *, proxy_api_key: str) -> dict[str, str]:
        return {
            "NODE_ENV": "production",
            "PORT": "8080",
            "CODEX_PROXY_HOST": "0.0.0.0",
            "PROXY_API_KEY": proxy_api_key,
            "NEXUS_MANAGED_RELEASE": "1",
        }

    def docker_security_args(self) -> list[str]:
        return [
            "--cap-drop",
            "ALL",
            "--cap-add",
            "SETUID",
            "--cap-add",
            "SETGID",
            "--cap-add",
            "CHOWN",
        ]

    def docker_volumes(self, *, runtime: ProviderRuntimeAccount) -> list[tuple[Path, str]]:
        root = runtime_storage_root(runtime=runtime)
        return [(root / "data", "/app/data"), (root / "config", "/app/config")]

    def login_url(self, *, host_port: int, host: str = "127.0.0.1") -> str:
        return f"http://{host}:{host_port}/auth/login"

    def api_base_url(self, *, host_port: int, host: str = "127.0.0.1") -> str:
        return f"http://{host}:{host_port}/v1"

    def health_urls(self, *, host_port: int, host: str = "127.0.0.1") -> list[str]:
        return [f"http://{host}:{host_port}/health", f"http://{host}:{host_port}/v1/models"]

    def browser_login_url(
        self, *, runtime: ProviderRuntimeAccount, host_port: int, host: str = "127.0.0.1"
    ) -> str:
        return self.login_url(host_port=host_port, host=host)

    def login_with_credentials(self, *, runtime: ProviderRuntimeAccount, username: str, password: str) -> ProviderRuntimeLoginResult:
        return ProviderRuntimeLoginResult(
            success=False,
            login_required=True,
            reason="codex_proxy supports OAuth/device interactive login; username/password login API is not available",
        )


class CLIProxyAPIRuntimeAdapter:
    runtime_type = ProviderRuntimeAccount.RUNTIME_CLIPROXYAPI
    container_port = 8317

    def image_name(self, *, runtime: ProviderRuntimeAccount) -> str:
        prefix = str(getattr(settings, "NEXUS_CLIPROXYAPI_IMAGE_PREFIX", "nexus-cliproxyapi"))
        return f"{prefix}:{shared_runtime_image_tag()}"

    def source_dir(self) -> Path:
        return Path(getattr(settings, "NEXUS_CLIPROXYAPI_SOURCE_DIR")).resolve()

    def prepare_storage(self, *, runtime: ProviderRuntimeAccount, proxy_api_key: str) -> None:
        root = runtime_storage_root(runtime=runtime)
        (root / "auths").mkdir(parents=True, exist_ok=True)
        (root / "logs").mkdir(parents=True, exist_ok=True)
        management_key = cliproxyapi_management_key(proxy_api_key=proxy_api_key)
        merge_runtime_config(root / "config.yaml", {
            "host": "", "port": 8317,
            "tls": {"enable": False},
            "remote-management": {"allow-remote": True, "secret-key": management_key},
            "auth-dir": "/root/.cli-proxy-api", "api-keys": [proxy_api_key],
        }, defaults={"debug": False, "usage-statistics-enabled": False})

    def docker_env(self, *, proxy_api_key: str) -> dict[str, str]:
        return {"MANAGEMENT_PASSWORD": cliproxyapi_management_key(proxy_api_key=proxy_api_key)}

    def docker_security_args(self) -> list[str]:
        return [
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
        ]

    def docker_volumes(self, *, runtime: ProviderRuntimeAccount) -> list[tuple[Path, str]]:
        root = runtime_storage_root(runtime=runtime)
        return [
            (root / "config.yaml", "/CLIProxyAPI/config.yaml"),
            (root / "auths", "/root/.cli-proxy-api"),
            (root / "logs", "/CLIProxyAPI/logs"),
        ]

    def login_url(self, *, host_port: int, host: str = "127.0.0.1") -> str:
        return f"http://{host}:{host_port}/v0/management"

    def api_base_url(self, *, host_port: int, host: str = "127.0.0.1") -> str:
        return f"http://{host}:{host_port}/v1"

    def health_urls(self, *, host_port: int, host: str = "127.0.0.1") -> list[str]:
        return [f"http://{host}:{host_port}/v1/models", f"http://{host}:{host_port}/models"]

    def browser_login_url(
        self, *, runtime: ProviderRuntimeAccount, host_port: int, host: str = "127.0.0.1"
    ) -> str:
        provider = cliproxyapi_oauth_provider(runtime=runtime)
        endpoint = "anthropic-auth-url" if provider == "anthropic" else "codex-auth-url"
        return f"http://{host}:{host_port}/v0/management/{endpoint}?is_webui=1"

    def login_with_credentials(self, *, runtime: ProviderRuntimeAccount, username: str, password: str) -> ProviderRuntimeLoginResult:
        return ProviderRuntimeLoginResult(
            success=False,
            login_required=True,
            reason="CLIProxyAPI requires interactive management login/import; username/password login API is not available",
        )


class DockerProviderRuntimeRunner(BaseProviderRuntimeRunner):
    def start(self, *, runtime: ProviderRuntimeAccount, proxy_api_key: str) -> ProviderRuntimeStartResult:  # pragma: no cover - integration surface
        adapter = adapter_for_runtime(runtime.runtime_type)
        release = approved_release(runtime.runtime_type)
        image = release["image_id"] if release else adapter.image_name(runtime=runtime)
        if release:
            # Fail before config writes or removing a working container.
            verify_image(release)
        elif should_build_images() and not (reuse_built_images() and docker_image_exists(image=image)):
            build_image(image=image, context=adapter.source_dir())
        adapter.prepare_storage(runtime=runtime, proxy_api_key=proxy_api_key)
        _remove_provider_runtime_containers(runtime=runtime)
        # Let Docker reserve an ephemeral host port atomically. Binding a
        # temporary socket here and releasing it before `docker run` left a
        # race where concurrent Runtime starts could claim the same port.
        host_port = 0
        command = docker_run_command(runtime=runtime, adapter=adapter, image=image, proxy_api_key=proxy_api_key, host_port=host_port)
        container_id = _run_docker(command)
        if not uses_container_network_endpoints():
            host_port = _docker_host_port(container_id=container_id, container_port=adapter.container_port)
        endpoint_host, endpoint_port = provider_runtime_endpoint(runtime=runtime, adapter=adapter, host_port=host_port)
        try:
            wait_for_runtime_port(adapter=adapter, host_port=endpoint_port, host=endpoint_host)
        except Exception:
            # A container that never becomes ready must not survive as an
            # unmanaged process after the lifecycle is marked failed.
            _docker_stop(container_id)
            raise
        return ProviderRuntimeStartResult(
            container_id=container_id,
            internal_login_url=adapter.browser_login_url(
                runtime=runtime, host_port=endpoint_port, host=endpoint_host
            ),
            internal_api_url=adapter.api_base_url(host_port=endpoint_port, host=endpoint_host),
        )

    def restore(self, *, runtime: ProviderRuntimeAccount, proxy_api_key: str) -> ProviderRuntimeStartResult:  # pragma: no cover - integration surface
        adapter = adapter_for_runtime(runtime.runtime_type)
        release = approved_release(runtime.runtime_type)
        if release:
            verify_image(release)
        container_id = _provider_runtime_container_id(runtime=runtime)
        if release and container_id:
            verify_existing_container(release, container_id)
        if container_id and _container_has_managed_restart_policy(container_id=container_id):
            was_running = _container_is_running(container_id=container_id)
            # Codex reads its persisted YAML, not PROXY_API_KEY. Synchronize
            # only our managed fields before adopting an existing container;
            # keep the account store and all unrelated configuration intact.
            config_changed = False
            if runtime.runtime_type == ProviderRuntimeAccount.RUNTIME_CODEX_PROXY:
                config_changed = adapter.prepare_storage(runtime=runtime, proxy_api_key=proxy_api_key)
            if not was_running:
                _run_docker(["docker", "start", container_id], timeout=30)
            elif config_changed:
                _run_docker(["docker", "restart", container_id], timeout=30)
            # Recover the current endpoint before probing: Docker Desktop may
            # republish a different port while the database still has the old one.
            if uses_container_network_endpoints():
                endpoint_host, endpoint_port = provider_runtime_container_name(runtime=runtime), adapter.container_port
            else:
                endpoint_host = "127.0.0.1"
                try:
                    endpoint_port = _docker_host_port(container_id=container_id, container_port=adapter.container_port)
                except exceptions.APIException:
                    return self.start(runtime=runtime, proxy_api_key=proxy_api_key)
            if was_running and not config_changed:
                probe_runtime = copy(runtime)
                probe_runtime.container_id = container_id
                probe_runtime.internal_api_url = adapter.api_base_url(host_port=endpoint_port, host=endpoint_host)
                health = self.health_check(runtime=probe_runtime)
                if (runtime.runtime_type == ProviderRuntimeAccount.RUNTIME_CLIPROXYAPI
                        and health.recovery_recommended
                        and health.reason.startswith("PROVIDER_MANAGED_CREDENTIAL_MISMATCH:")):
                    # Its immutable env and single-file mount cannot be fixed
                    # with restart alone. start() preserves the OAuth directory.
                    return self.start(runtime=runtime, proxy_api_key=proxy_api_key)
                if health.recovery_recommended and health.reason.startswith("PROVIDER_ENDPOINT_MISMATCH:"):
                    # A stale Docker Desktop forward can answer HTTP 200 from
                    # the wrong process. Restarting with the same published
                    # port retains that binding. Reallocate only this Runtime;
                    # start() preserves its mounted login store and proxy key.
                    return self.start(runtime=runtime, proxy_api_key=proxy_api_key)
                if health.recovery_recommended and not health.healthy and not health.login_required:
                    # A running container can contain a wedged local proxy. Do
                    # not restart for an upstream HTTP error or expired login.
                    _run_docker(["docker", "restart", container_id], timeout=30)
            if uses_container_network_endpoints():
                endpoint_host, endpoint_port = provider_runtime_container_name(runtime=runtime), adapter.container_port
            else:
                endpoint_host = "127.0.0.1"
                try:
                    endpoint_port = _docker_host_port(container_id=container_id, container_port=adapter.container_port)
                except exceptions.APIException:
                    # Docker Desktop can retain HostConfig.PortBindings while
                    # losing the effective binding after an engine restart.
                    # Recreate this exact Runtime with a new allocated port;
                    # start() reuses its private storage and proxy credential.
                    return self.start(runtime=runtime, proxy_api_key=proxy_api_key)
            wait_for_runtime_port(adapter=adapter, host_port=endpoint_port, host=endpoint_host)
            return ProviderRuntimeStartResult(
                container_id=container_id,
                internal_login_url=adapter.browser_login_url(
                    runtime=runtime, host_port=endpoint_port, host=endpoint_host
                ),
                internal_api_url=adapter.api_base_url(host_port=endpoint_port, host=endpoint_host),
            )

        # Containers created by older releases used --rm and had no restart
        # policy. Recreate only the container bound to this exact Runtime while
        # preserving its storage and proxy key.
        return self.start(runtime=runtime, proxy_api_key=proxy_api_key)

    def stop(self, *, runtime: ProviderRuntimeAccount) -> None:  # pragma: no cover - integration surface
        if runtime.container_id:
            _docker_stop(runtime.container_id)

    def stop_restored_container(self, *, runtime: ProviderRuntimeAccount, container_id: str) -> None:
        if runtime.status not in {ProviderRuntimeAccount.STATUS_STOPPED, "deleted"}:
            raise exceptions.APIException("Restored container cleanup requires a terminal runtime.")
        if not re.fullmatch(r"[a-f0-9]{64}", container_id):
            raise exceptions.APIException("Restored container cleanup requires an exact Docker container ID.")
        owner = _run_docker([
            "docker", "inspect", "-f", '{{index .Config.Labels "nexus.provider_runtime_id"}}', container_id,
        ], timeout=10).strip()
        if owner != str(runtime.id):
            raise exceptions.APIException("Restored container does not belong to this Provider Runtime.")
        _docker_stop(container_id)

    def health_check(self, *, runtime: ProviderRuntimeAccount) -> ProviderRuntimeHealthResult:  # pragma: no cover - integration surface
        if not runtime.container_id:
            return ProviderRuntimeHealthResult(False, False, "container is not running", True)
        state = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", runtime.container_id],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if state.returncode != 0:
            # Do not expose Docker's raw stderr (host paths / daemon details).
            return ProviderRuntimeHealthResult(False, False, "docker inspect failed", True)
        if state.stdout.strip().lower() != "true":
            return ProviderRuntimeHealthResult(False, False, "container is stopped", True)
        if not runtime.internal_api_url:
            return ProviderRuntimeHealthResult(False, False, "runtime API URL is missing", True)
        if runtime.runtime_type == ProviderRuntimeAccount.RUNTIME_CODEX_PROXY:
            semantic = probe_codex_endpoint_health(runtime=runtime)
            if not semantic.recovery_recommended and not semantic.inconclusive:
                authentication = probe_codex_auth_health(runtime=runtime)
                if not authentication.healthy:
                    return authentication
            if not semantic.healthy:
                return semantic
        elif runtime.runtime_type == ProviderRuntimeAccount.RUNTIME_CLIPROXYAPI:
            semantic = probe_cliproxyapi_auth_health(runtime=runtime)
            if semantic.inconclusive:
                transport = probe_openai_compatible_health(runtime=runtime)
                if transport.recovery_recommended:
                    return transport
            if not semantic.healthy:
                return semantic
        return probe_openai_compatible_health(runtime=runtime)

    def login_with_credentials(self, *, runtime: ProviderRuntimeAccount, username: str, password: str) -> ProviderRuntimeLoginResult:  # pragma: no cover - integration surface
        if not username or not password:
            return ProviderRuntimeLoginResult(False, True, "username and password are required")
        return adapter_for_runtime(runtime.runtime_type).login_with_credentials(runtime=runtime, username=username, password=password)


def adapter_for_runtime(runtime_type: str) -> LoginProviderRuntimeAdapter:
    if runtime_type == ProviderRuntimeAccount.RUNTIME_CODEX_PROXY:
        return CodexProxyRuntimeAdapter()
    if runtime_type == ProviderRuntimeAccount.RUNTIME_CLIPROXYAPI:
        return CLIProxyAPIRuntimeAdapter()
    raise exceptions.ValidationError("Unsupported provider runtime type.")


def docker_run_command(
    *,
    runtime: ProviderRuntimeAccount,
    adapter: LoginProviderRuntimeAdapter,
    image: str,
    proxy_api_key: str,
    host_port: int,
) -> list[str]:
    command = [
        "docker",
        "run",
        "-d",
        "--name",
        provider_runtime_container_name(runtime=runtime),
        "--restart",
        "unless-stopped",
        *adapter.docker_security_args(),
        "--memory",
        str(getattr(settings, "NEXUS_PROVIDER_RUNTIME_MEMORY_LIMIT", "1024m")),
        "--pids-limit",
        str(getattr(settings, "NEXUS_PROVIDER_RUNTIME_PIDS_LIMIT", 512)),
        "--label",
        "nexus.provider_runtime=true",
        "--label",
        f"nexus.provider_runtime_id={runtime.id}",
        "--label",
        f"nexus.tenant_id={runtime.tenant_id}",
        "--network",
        str(getattr(settings, "NEXUS_PROVIDER_RUNTIME_DOCKER_NETWORK", "bridge")),
    ]
    if not uses_container_network_endpoints():
        published_port = str(host_port) if host_port > 0 else ""
        command.extend(["-p", f"127.0.0.1:{published_port}:{adapter.container_port}"])
    for key, value in adapter.docker_env(proxy_api_key=proxy_api_key).items():
        command.extend(["-e", f"{key}={value}"])
    for controller_path, container_path in adapter.docker_volumes(runtime=runtime):
        command.extend(["-v", f"{docker_host_storage_path(controller_path)}:{container_path}"])
    command.append(image)
    return command


def build_image(*, image: str, context: Path) -> None:
    if not context.exists() or not (context / "Dockerfile").exists():
        raise exceptions.APIException(f"Provider runtime source directory is missing a Dockerfile: {context}")
    _run_docker(["docker", "build", "-t", image, str(context)], timeout=int(getattr(settings, "NEXUS_PROVIDER_RUNTIME_BUILD_TIMEOUT_SECONDS", 1800)))


def docker_image_exists(*, image: str) -> bool:
    result = _docker_subprocess(["docker", "image", "inspect", image], capture_output=True, text=True, timeout=30)
    return result.returncode == 0


def wait_for_runtime_port(
    *, adapter: LoginProviderRuntimeAdapter, host_port: int, host: str = "127.0.0.1"
) -> None:
    attempts = int(getattr(settings, "NEXUS_PROVIDER_RUNTIME_START_ATTEMPTS", 60))
    delay = float(getattr(settings, "NEXUS_PROVIDER_RUNTIME_START_DELAY_SECONDS", 0.5))
    for _ in range(attempts):
        for url in [
            adapter.login_url(host_port=host_port, host=host),
            *adapter.health_urls(host_port=host_port, host=host),
        ]:
            if http_endpoint_reachable(url=url):
                return
        time.sleep(delay)
    raise exceptions.APIException("Provider runtime HTTP endpoint did not become reachable.")


def probe_openai_compatible_health(*, runtime: ProviderRuntimeAccount) -> ProviderRuntimeHealthResult:
    url = runtime.internal_api_url.rstrip("/") + "/models"
    proxy_api_key = decrypt_secret(runtime.encrypted_proxy_api_key) if runtime.encrypted_proxy_api_key else ""
    headers = {"Authorization": f"Bearer {proxy_api_key}"} if proxy_api_key else {}
    status_code, reason = http_status(url=url, headers=headers)
    if status_code is None:
        # A busy host can miss the fast probe while its proxy is still healthy.
        # Confirm transport failure with a bounded, more patient GET before
        # recommending a destructive restart. Never replay model invocations or
        # HTTP authentication/upstream errors here.
        retry_timeout = min(30.0, max(2.0, float(getattr(
            settings, "NEXUS_PROVIDER_HEALTH_CONFIRM_TIMEOUT_SECONDS", 15
        ))))
        status_code, reason = http_status(url=url, headers=headers, timeout=retry_timeout)
    if (
        status_code == 401
        and reason == "PROVIDER_LOCAL_API_KEY_REJECTED"
        and getattr(runtime, "runtime_type", "") in {
            ProviderRuntimeAccount.RUNTIME_CODEX_PROXY, ProviderRuntimeAccount.RUNTIME_CLIPROXYAPI,
        }
    ):
        return ProviderRuntimeHealthResult(
            False, False,
            "PROVIDER_MANAGED_CREDENTIAL_MISMATCH: Managed proxy API credential requires automatic synchronization.",
            recovery_recommended=True,
        )
    if status_code and 200 <= status_code < 500:
        return ProviderRuntimeHealthResult(
            healthy=status_code < 400,
            login_required=status_code in {401, 403},
            reason=f"runtime returned HTTP {status_code}",
        )
    if status_code is None:
        return ProviderRuntimeHealthResult(False, False, "Runtime transport is unavailable.", True)
    return ProviderRuntimeHealthResult(False, False, f"runtime returned HTTP {status_code}")


class _NoHealthRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe_cliproxyapi_auth_health(*, runtime) -> ProviderRuntimeHealthResult:
    """Read safe auth status, not the image-only fallback catalog or credentials.

    An unavailable management check is not evidence of expired OAuth. Never
    restart a process or expose the upstream status_message on this path.
    """
    unknown = ProviderRuntimeHealthResult(
        False, False, "PROVIDER_AUTH_CHECK_PENDING: Sign-in status could not be checked; retry scheduled.",
        inconclusive=True,
    )
    try:
        parts = urlsplit(runtime.internal_api_url)
        if parts.scheme not in {"http", "https"} or parts.username or parts.password:
            return unknown
        key = decrypt_secret(runtime.encrypted_proxy_api_key)
        if not key:
            return unknown
        request = Request(
            urlunsplit((parts.scheme, parts.netloc, "/v0/management/auth-files", "", "")),
            headers={"X-Management-Key": cliproxyapi_management_key(proxy_api_key=key)},
        )
        with build_opener(ProxyHandler({}), _NoHealthRedirect()).open(request, timeout=5) as response:
            body = response.read(262145)
        if len(body) > 262144:
            return unknown
        rows = json.loads(body).get("files")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            return unknown
    except HTTPError as exc:
        try:
            body = exc.read(1025) if exc.code == 401 else b""
            error = json.loads(body).get("error") if len(body) <= 1024 else None
            mismatch = isinstance(error, str) and error in {"invalid management key", "missing management key"}
        except (ValueError, AttributeError, OSError):
            mismatch = False
        finally:
            exc.close()
        if mismatch:
            return ProviderRuntimeHealthResult(
                False, False,
                "PROVIDER_MANAGED_CREDENTIAL_MISMATCH: Managed proxy credential requires automatic synchronization.",
                recovery_recommended=True,
            )
        return unknown
    except (ValueError, AttributeError, OSError, RemoteDisconnected):
        return unknown
    enabled = [row for row in rows if not row.get("disabled")]
    if any(row.get("status") == "active" and not row.get("unavailable") for row in enabled):
        return ProviderRuntimeHealthResult(True, False, "Provider sign-in is available.")
    expired = ("expired", "invalid_grant", "revoked", "refresh_token_reused", "reauth", "token_invalidated")
    if not enabled or all(any(code in str(row.get("status_message", "")).lower() for code in expired) for row in enabled):
        return ProviderRuntimeHealthResult(
            False, True, "PROVIDER_LOGIN_REQUIRED: Provider sign-in is missing or expired. Sign in again to restore text models.",
        )
    return ProviderRuntimeHealthResult(
        False, False, "PROVIDER_ACCOUNT_UNAVAILABLE: Review Provider quota and account status; automatic checks will continue.",
    )


def _codex_health_summary(payload):
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return None
    pool = payload.get("pool")
    if not isinstance(pool, dict):
        return None
    total, authenticated = pool.get("total"), payload.get("authenticated")
    if type(total) is not int or total < 0 or type(authenticated) is not bool:
        return None
    return total, authenticated


def _codex_host_health(*, runtime, timeout=5):
    # No credentials, system proxy, or redirects in endpoint diagnostics.
    # Read only a bounded local health document, never the inference endpoint.
    try:
        parts = urlsplit(runtime.internal_api_url)
        if parts.scheme not in {"http", "https"} or parts.username or parts.password:
            return None
        request = Request(urlunsplit((parts.scheme, parts.netloc, "/health", "", "")))
        with build_opener(ProxyHandler({}), _NoHealthRedirect()).open(request, timeout=timeout) as response:
            body = response.read(4097)
        if len(body) > 4096:
            return None
        return json.loads(body)
    except HTTPError:
        return None
    except (URLError, TimeoutError, ConnectionError, RemoteDisconnected):
        return {"transport_unavailable": True}
    except (ValueError, OSError):
        return None


def _codex_container_health(*, runtime, timeout=5):
    # Keep sensitive fields out of stdout even if the upstream health payload
    # expands. The subprocess is fixed code, not a shell or user-supplied URL.
    script = """(async()=>{try{
let r;try{r=await fetch('http://127.0.0.1:8080/health',{redirect:'error',signal:AbortSignal.timeout(5000)});}
catch(e){if(e.name==='TimeoutError'||e.name==='AbortError'||e.cause?.code==='ECONNREFUSED'||e.cause?.code==='ECONNRESET'){
process.stdout.write(JSON.stringify({transport_unavailable:true}),()=>process.exit(0));return;}throw e;}
if(!r.ok)throw Error();let b='',n=0;
for await(const chunk of r.body){n+=chunk.length;if(n>4096)throw Error();b+=Buffer.from(chunk).toString('utf8');}
const d=JSON.parse(b);process.stdout.write(JSON.stringify({status:d.status,authenticated:d.authenticated,pool:{total:d.pool?.total}}),()=>process.exit(0));
}catch{process.exit(1);}})();""".replace("5000", str(int(timeout * 1000)))
    try:
        result = subprocess.run(
            ["docker", "exec", runtime.container_id, "node", "-e", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout + 5,
        )
        if result.returncode or len(result.stdout) > 4096:
            return None
        return json.loads(result.stdout)
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return None


def probe_codex_auth_health(*, runtime) -> ProviderRuntimeHealthResult:
    """Verify managed dashboard authentication separately from upstream OAuth.

    A successful /health response is anonymous. Only the proxy's exact local
    authentication rejection authorizes repairing its managed credential.
    Never read/log a successful /auth/status document: it can contain secrets.
    """
    if not runtime.encrypted_proxy_api_key:
        # Legacy uncredentialed health fixtures/controllers still use the
        # semantic probe. Lifecycle reconciliation validates production keys.
        return ProviderRuntimeHealthResult(True, False, "Managed credential probe not configured.")
    unknown = ProviderRuntimeHealthResult(
        False, False, "PROVIDER_AUTH_CHECK_PENDING: Managed authentication verification unavailable; retry scheduled.",
        inconclusive=True,
    )
    try:
        parts = urlsplit(runtime.internal_api_url)
        if parts.scheme not in {"http", "https"} or parts.username or parts.password:
            return unknown
        key = decrypt_secret(runtime.encrypted_proxy_api_key)
        if not key:
            return unknown
        request = Request(
            urlunsplit((parts.scheme, parts.netloc, "/auth/status", "", "")),
            headers={"Authorization": f"Bearer {key}"},
        )
        with build_opener(ProxyHandler({}), _NoHealthRedirect()).open(request, timeout=5) as response:
            if response.status == 200:
                return ProviderRuntimeHealthResult(True, False, "Managed authentication verified.")
        return unknown
    except HTTPError as exc:
        try:
            body = exc.read(1025) if exc.code == 401 else b""
            rejected = len(body) <= 1024 and json.loads(body).get("error") == "Dashboard login required"
        except (ValueError, AttributeError, OSError):
            rejected = False
        finally:
            exc.close()
        if rejected:
            return ProviderRuntimeHealthResult(
                False, False,
                "PROVIDER_MANAGED_CREDENTIAL_MISMATCH: Managed proxy credential requires automatic synchronization.",
                recovery_recommended=True,
            )
        return unknown
    except (ValueError, OSError, URLError, RemoteDisconnected):
        return unknown


def probe_codex_endpoint_health(*, runtime) -> ProviderRuntimeHealthResult:
    previous = None
    for attempt in range(2):
        timeout = 5 if attempt == 0 else min(30.0, max(5.0, float(getattr(
            settings, "NEXUS_PROVIDER_HEALTH_CONFIRM_TIMEOUT_SECONDS", 15
        ))))
        host_payload = _codex_host_health(runtime=runtime, timeout=timeout)
        host = _codex_health_summary(host_payload)
        container_payload = _codex_container_health(runtime=runtime, timeout=timeout)
        container = _codex_health_summary(container_payload)
        if host_payload == {"transport_unavailable": True} and container_payload == {"transport_unavailable": True}:
            if previous == "local-transport":
                return ProviderRuntimeHealthResult(False, False, "Runtime transport is unavailable.", True)
            previous = "local-transport"
            continue
        if host_payload == {"transport_unavailable": True} and container is not None:
            if previous == "transport":
                return ProviderRuntimeHealthResult(False, False, "Runtime transport is unavailable.", True)
            previous = "transport"
            continue
        if host is None or container is None:
            if attempt == 0:
                continue
            return ProviderRuntimeHealthResult(False, False, "PROVIDER_ENDPOINT_CHECK_PENDING: Endpoint verification is temporarily unavailable; retry scheduled. Known model results are retained.", inconclusive=True)
        if host == container:
            if host[1]:
                return ProviderRuntimeHealthResult(True, False, "Provider endpoint verified.")
            if host[0] == 0:
                return ProviderRuntimeHealthResult(False, True, "Sign in to this Provider to load its model catalog.")
            # authenticated also becomes false for exhausted quota or cooldown;
            # never destroy sessions or incorrectly demand login in that case.
            return ProviderRuntimeHealthResult(False, False, "Provider has no usable account; review quota and sign-in status. Automatic checks will continue.")
        pair = (host, container)
        if host[0] != container[0] and pair == previous:
            return ProviderRuntimeHealthResult(False, False,
                "PROVIDER_ENDPOINT_MISMATCH: Published endpoint differs from its container; automatic port recovery required.", True)
        previous = pair
    return ProviderRuntimeHealthResult(False, False, "Provider endpoint state is changing; retry scheduled.", inconclusive=True)


def http_endpoint_reachable(*, url: str) -> bool:
    status_code, _reason = http_status(url=url)
    return bool(status_code and status_code < 500)


def http_status(*, url: str, headers: dict[str, str] | None = None, timeout: float = 2) -> tuple[int | None, str]:
    request = Request(url, headers=headers or {}, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            # Health needs only the HTTP status, not a potentially large or
            # slowly streamed model catalog. The context manager closes it.
            return response.status, response.reason
    except HTTPError as exc:
        try:
            # CLIProxyAPI's access middleware uses these exact local errors.
            # Nested upstream authentication errors, malformed/large payloads,
            # and every other status remain ordinary HTTP failures, not proof
            # that a managed proxy key can be safely repaired.
            if exc.code == 401:
                body = exc.read(1025)
                payload = json.loads(body) if len(body) <= 1024 else None
                error = payload.get("error") if isinstance(payload, dict) else None
                if isinstance(error, str) and error in {"Invalid API key", "Missing API key"}:
                    return exc.code, "PROVIDER_LOCAL_API_KEY_REJECTED"
        except (ValueError, AttributeError, OSError):
            pass
        finally:
            exc.close()
        return exc.code, f"HTTP {exc.code}"
    except (RemoteDisconnected, URLError, TimeoutError, OSError) as exc:
        return None, str(exc)


def runtime_storage_root(*, runtime: ProviderRuntimeAccount) -> Path:
    root = Path(getattr(settings, "NEXUS_PROVIDER_RUNTIME_STORAGE_ROOT")).resolve()
    storage_path = runtime.storage_path or f"{runtime.tenant_id}/{runtime.id}"
    target = (root / storage_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise exceptions.APIException("Provider runtime storage path is outside storage root.") from exc
    target.mkdir(parents=True, exist_ok=True)
    return target


def should_build_images() -> bool:
    return bool(getattr(settings, "NEXUS_PROVIDER_RUNTIME_BUILD_IMAGES", True))


def shared_runtime_image_tag() -> str:
    return str(getattr(settings, "NEXUS_PROVIDER_RUNTIME_SHARED_IMAGE_TAG", "")).strip() or "runtime"


def cliproxyapi_oauth_provider(*, runtime: ProviderRuntimeAccount) -> str:
    source = runtime.source_provider_account
    provider_name = source.provider.name.lower() if source is not None and source.provider_id else ""
    return "anthropic" if provider_name == "claude" else "codex"


def reuse_built_images() -> bool:
    return bool(getattr(settings, "NEXUS_PROVIDER_RUNTIME_REUSE_BUILT_IMAGES", True))


def get_provider_runtime_runner() -> BaseProviderRuntimeRunner:
    runner = str(getattr(settings, "NEXUS_PROVIDER_RUNTIME_RUNNER", "fake")).lower()
    if runner == "docker":
        return DockerProviderRuntimeRunner()
    if runner == "controller":
        from .provider_controller import ControllerProviderRuntimeRunner

        return ControllerProviderRuntimeRunner()
    if runner == "fake":
        if getattr(settings, "NEXUS_PRODUCTION", False):
            raise ImproperlyConfigured("Production Provider execution cannot use the fake runtime runner.")
        return FakeProviderRuntimeRunner()
    raise ImproperlyConfigured(f"Unsupported NEXUS_PROVIDER_RUNTIME_RUNNER: {runner}")


def _docker_subprocess(command, **kwargs):
    """Preserve each CLI boundary while sharing conservative outage typing."""
    try:
        result = subprocess.run(command, **kwargs)
    except PermissionError:
        raise exceptions.APIException("Docker access was denied. Review controller permissions.") from None
    except (OSError, subprocess.TimeoutExpired):
        # A timed-out Docker CLI may already have created the container. The
        # caller schedules observation/adoption, never blindly repeats Start.
        raise ProviderRuntimeUnavailable() from None
    if docker_daemon_transport_unavailable(result):
        raise ProviderRuntimeUnavailable() from None
    return result


def _run_docker(command: list[str], timeout: int | float = 60) -> str:
    result = _docker_subprocess(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode != 0:
        raise exceptions.APIException((result.stderr or result.stdout or "Docker command failed.").strip())
    return result.stdout.strip()


def _docker_host_port(*, container_id: str, container_port: int) -> int:
    output = _run_docker(["docker", "port", container_id, f"{container_port}/tcp"])
    try:
        first = output.splitlines()[0].strip()
        return int(first.rsplit(":", 1)[1])
    except (IndexError, ValueError) as exc:
        raise exceptions.APIException("Docker did not publish the provider runtime port.") from exc


def provider_runtime_container_name(*, runtime: ProviderRuntimeAccount) -> str:
    return f"nexus-provider-runtime-{runtime.id}"


def uses_container_network_endpoints() -> bool:
    return bool(getattr(settings, "NEXUS_PROVIDER_RUNTIME_NETWORK_ENDPOINTS", False))


def provider_runtime_endpoint(
    *, runtime: ProviderRuntimeAccount, adapter: LoginProviderRuntimeAdapter, host_port: int
) -> tuple[str, int]:
    if uses_container_network_endpoints():
        return provider_runtime_container_name(runtime=runtime), adapter.container_port
    return "127.0.0.1", host_port


def docker_host_storage_path(controller_path: Path) -> Path:
    controller_path = controller_path.resolve()
    host_root_value = str(getattr(settings, "NEXUS_PROVIDER_RUNTIME_HOST_STORAGE_ROOT", "")).strip()
    if not host_root_value:
        return controller_path
    controller_root = Path(getattr(settings, "NEXUS_PROVIDER_RUNTIME_STORAGE_ROOT")).resolve()
    try:
        relative = controller_path.relative_to(controller_root)
    except ValueError as exc:
        raise exceptions.APIException("Provider runtime volume is outside the configured storage root.") from exc
    return Path(host_root_value).resolve() / relative


def _provider_runtime_container_ids(*, runtime: ProviderRuntimeAccount) -> list[str]:
    result = _docker_subprocess(
        ["docker", "ps", "-aq", "--no-trunc", "--filter", f"label=nexus.provider_runtime_id={runtime.id}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if result.returncode != 0:
        raise exceptions.APIException((result.stderr or "Docker runtime lookup failed.").strip())
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _provider_runtime_container_id(*, runtime: ProviderRuntimeAccount) -> str:
    candidates = []
    if runtime.container_id:
        inspected = _docker_subprocess(
            ["docker", "inspect", runtime.container_id], capture_output=True, text=True, timeout=10
        )
        if inspected.returncode == 0:
            candidates.append(runtime.container_id)
    for container_id in _provider_runtime_container_ids(runtime=runtime):
        if container_id not in candidates:
            candidates.append(container_id)
    return candidates[0] if candidates else ""


def _container_is_running(*, container_id: str) -> bool:
    result = _docker_subprocess(
        ["docker", "inspect", "-f", "{{.State.Running}}", container_id],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.returncode == 0 and result.stdout.strip().lower() == "true"


def _container_has_managed_restart_policy(*, container_id: str) -> bool:
    result = _docker_subprocess(
        [
            "docker",
            "inspect",
            "-f",
            "{{.HostConfig.AutoRemove}}|{{.HostConfig.RestartPolicy.Name}}",
            container_id,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.returncode == 0 and result.stdout.strip().lower() == "false|unless-stopped"


def _remove_provider_runtime_containers(*, runtime: ProviderRuntimeAccount) -> None:
    for container_id in _provider_runtime_container_ids(runtime=runtime):
        _docker_stop(container_id)


def _docker_stop(container_id: str) -> None:
    result = _docker_subprocess(
        ["docker", "rm", "-f", container_id],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if result.returncode == 0:
        return
    detail = (result.stderr or result.stdout or "Docker runtime removal failed.").strip()
    if "no such container" in detail.lower():
        return
    raise exceptions.APIException(detail)


def cliproxyapi_management_key(*, proxy_api_key: str) -> str:
    return f"mgmt-{proxy_api_key}"
