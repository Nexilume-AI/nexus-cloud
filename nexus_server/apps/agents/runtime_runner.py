from __future__ import annotations

import json
import os
import re
import socket
import ssl
import subprocess
import time
import uuid
from pathlib import Path
from dataclasses import dataclass, field
from datetime import timedelta
from collections.abc import Iterator
from http.client import BadStatusLine, HTTPException, HTTPSConnection, RemoteDisconnected
from socket import timeout as SocketTimeout
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen, HTTPRedirectHandler, build_opener

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import exceptions

from .models import AgentRuntimeDeployment


@dataclass(frozen=True)
class RuntimeStartResult:
    container_id: str
    internal_mcp_url: str


@dataclass(frozen=True)
class RuntimeDisplayContext:
    run_id: str
    events_url: str
    write_token: str = field(repr=False)
    public_url: str = ""
    display_token: str = field(default="", repr=False)
    display_url: str = ""
    computer_enabled: bool = False
    terminal_url: str = ""
    workspace_url: str = ""
    memory_url: str = ""
    workspace_root: str = ""
    output_root: str = ""
    workspace_delegate_url: str = ""
    workspace_delegate_token: str = field(default="", repr=False)
    workspace_capabilities: tuple[str, ...] = ()
    browser_enabled: bool = False
    browser_delegate_url: str = ""
    browser_delegate_token: str = field(default="", repr=False)
    browser_computer_name: str = ""
    mobile_enabled: bool = False
    mobile_delegate_url: str = ""
    mobile_delegate_token: str = field(default="", repr=False)
    mobile_capabilities: tuple[str, ...] = ()
    interaction_url: str = ""
    context_url: str = ""
    context_token: str = field(default="", repr=False)
    checkpoint_url: str = ""
    recovery_url: str = ""
    recovery_managed: bool = False
    recovery_attempt: int = 0
    recovery_is_replay: bool = False
    recovery_last_committed_operation: int = 0
    display_asset_url: str = ""
    interaction_token: str = field(default="", repr=False)
    interaction_mode: str = ""
    turn_index: int = 1
    billing_url: str = ""
    billing_token: str = field(default="", repr=False)
    billing_currency: str = ""
    billing_max_cost: str = ""
    usage_url: str = ""
    execution_profile: str = ""
    execution_model: str = ""
    reasoning_effort: str = ""
    execution_context_window: int = 0
    input_files: tuple[dict[str, Any], ...] = ()
    cloud_trust: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class RuntimeWorkspaceContext:
    connection_id: str
    root: str
    access_mode: str
    api_url: str
    token: str


@dataclass(frozen=True)
class RuntimeMCPResult:
    status_code: int
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class RuntimeMCPStreamResult:
    status_code: int
    headers: dict[str, str]
    chunks: Iterator[bytes]


class AgentRuntimeConnectionFailed(exceptions.APIException):
    status_code = 502
    default_detail = "The Agent connection was lost."
    default_code = "AGENT_RUNTIME_CONNECTION_FAILED"


class AgentRuntimeStreamingUnsupported(exceptions.APIException):
    status_code = 400
    default_detail = "Agent runtime streaming is not supported."
    default_code = "AGENT_STREAMING_NOT_SUPPORTED"


class AgentDockerRuntimeError(exceptions.APIException):
    status_code = 503
    default_detail = "The Docker Agent runtime could not be started."
    default_code = "AGENT_DOCKER_RUNTIME_FAILED"

    def __init__(self, *, diagnostics: dict[str, Any] | None = None):
        super().__init__(self.default_detail, code=self.default_code)
        self.runtime_diagnostics = diagnostics or {}


class BaseAgentRuntimeRunner:
    def resolve_image(self, *, image_ref: str) -> str:
        return ""

    def load_image(self, *, artifact_path: str, image_ref: str = "") -> str:
        raise NotImplementedError

    def validate_image_ref(self, *, image_ref: str) -> None:
        validate_docker_image_reference(image_ref)

    def start(
        self,
        *,
        deployment: AgentRuntimeDeployment,
        display_context: RuntimeDisplayContext | None = None,
        workspace_context: RuntimeWorkspaceContext | None = None,
    ) -> RuntimeStartResult:
        raise NotImplementedError

    def stop(self, *, deployment: AgentRuntimeDeployment) -> None:
        raise NotImplementedError

    def health_check(self, *, deployment: AgentRuntimeDeployment) -> bool:
        raise NotImplementedError

    def diagnostics(self, *, deployment: AgentRuntimeDeployment) -> dict[str, Any]:
        return {}

    def sweep_orphans(self, *, valid_runtime_ids: set[str]) -> dict[str, int]:
        return {"containers": 0, "networks": 0}

    def call_mcp(self, *, deployment: AgentRuntimeDeployment, method: str, headers: dict[str, str], body: bytes, path: str = "", timeout: int | float | None = None) -> RuntimeMCPResult:
        raise NotImplementedError

    def stream_mcp(self, *, deployment: AgentRuntimeDeployment, method: str, headers: dict[str, str], body: bytes, path: str = "") -> RuntimeMCPStreamResult:
        raise AgentRuntimeStreamingUnsupported()


class FakeAgentRuntimeRunner(BaseAgentRuntimeRunner):
    def load_image(self, *, artifact_path: str, image_ref: str = "") -> str:
        return image_ref or f"loaded-agent:{uuid.uuid4().hex[:12]}"

    def start(
        self,
        *,
        deployment: AgentRuntimeDeployment,
        display_context: RuntimeDisplayContext | None = None,
        workspace_context: RuntimeWorkspaceContext | None = None,
    ) -> RuntimeStartResult:
        return RuntimeStartResult(
            container_id=f"fake_{uuid.uuid4().hex[:12]}",
            internal_mcp_url=f"http://agent-runtime.local/{deployment.agent_id}/{deployment.env}/mcp",
        )

    def stop(self, *, deployment: AgentRuntimeDeployment) -> None:
        return None

    def health_check(self, *, deployment: AgentRuntimeDeployment) -> bool:
        return deployment.status == AgentRuntimeDeployment.STATUS_ACTIVE

    def call_mcp(self, *, deployment: AgentRuntimeDeployment, method: str, headers: dict[str, str], body: bytes, path: str = "", timeout: int | float | None = None) -> RuntimeMCPResult:
        if method.upper() == "GET":
            return RuntimeMCPResult(
                status_code=405,
                headers={"Content-Type": "application/json"},
                body=_json_dumps(
                    {
                        "jsonrpc": "2.0",
                        "error": {"code": -32601, "message": "SSE stream is not available in fake runner"},
                        "id": None,
                    }
                ),
            )

        payload = _json_loads(body)
        request_id = payload.get("id")
        rpc_method = payload.get("method")
        if rpc_method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": headers.get("MCP-Protocol-Version") or "2025-06-18",
                "serverInfo": {
                    "name": deployment.agent.name,
                    "version": deployment.image.version.version if deployment.image.version else "",
                },
                "capabilities": {"tools": {}},
            }
        elif rpc_method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "run_agent",
                        "description": "Run the deployed Nexus agent.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "task": {"type": "string"},
                                "context": {"type": "string"},
                                "mode": {"type": "string", "enum": ["explain", "code", "verify"]},
                                "attachments": {
                                    "type": "array",
                                    "items": {"type": "object"},
                                    "description": "OpenAI-compatible multimodal content parts such as image_url.",
                                },
                            },
                            "required": ["task"],
                        },
                    }
                ]
            }
        elif rpc_method == "tools/call":
            params = payload.get("params") or {}
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            attachments = arguments.get("attachments") if isinstance(arguments.get("attachments"), list) else []
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"fake agent response from {deployment.agent.name}:{params.get('name', '')}; "
                            f"attachments={len(attachments)}"
                        ),
                    }
                ],
                "isError": False,
            }
        else:
            return RuntimeMCPResult(
                status_code=200,
                headers={"Content-Type": "application/json"},
                body=_json_dumps(
                    {
                        "jsonrpc": "2.0",
                        "error": {"code": -32601, "message": "Method not found"},
                        "id": request_id,
                    }
                ),
            )
        return RuntimeMCPResult(
            status_code=200,
            headers={"Content-Type": "application/json"},
            body=_json_dumps({"jsonrpc": "2.0", "result": result, "id": request_id}),
        )


class DockerAgentRuntimeRunner(BaseAgentRuntimeRunner):
    def resolve_image(self, *, image_ref: str) -> str:
        from .docker_policy import verify_admission
        validate_docker_image_reference(image_ref)
        info = _docker_inspect("image", image_ref)
        if info is None:
            _run_docker(["docker", "pull", image_ref], timeout=180)
            info = _docker_inspect("image", image_ref)
        digest = (info or {}).get("Id", "")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
            raise exceptions.ValidationError("Docker did not resolve an immutable image identity.")
        verify_admission(digest)
        return digest

    def validate_image_ref(self, *, image_ref: str) -> None:  # pragma: no cover - integration surface
        validate_docker_image_reference(image_ref)
        if not getattr(settings, "NEXUS_AGENT_RUNTIME_VERIFY_IMAGE_ON_REGISTER", True):
            return
        if not _docker_image_available(image_ref=image_ref):
            raise exceptions.ValidationError("Docker image is not available locally and its registry manifest could not be verified.")

    def load_image(self, *, artifact_path: str, image_ref: str = "") -> str:  # pragma: no cover - integration surface
        output = _run_docker(["docker", "load", "-i", artifact_path], timeout=180)
        loaded_ref = _parse_docker_load_ref(output)
        if image_ref and image_ref != loaded_ref:
            # Never silently substitute an unrelated pre-existing host image.
            supplied = _docker_inspect("image", image_ref)
            loaded = _docker_inspect("image", loaded_ref)
            if not supplied or not loaded or supplied.get("Id") != loaded.get("Id"):
                raise exceptions.ValidationError("Uploaded image does not match the requested image reference.")
        return image_ref or loaded_ref

    def start(
        self,
        *,
        deployment: AgentRuntimeDeployment,
        display_context: RuntimeDisplayContext | None = None,
        workspace_context: RuntimeWorkspaceContext | None = None,
    ) -> RuntimeStartResult:  # pragma: no cover - integration surface
        from .docker_policy import assert_host, container_name, host_id, resource_limits, verify_admission
        assert_host(deployment)
        limits = (getattr(deployment, "docker_lifecycle", {}) or {}).get("resources") or resource_limits(deployment)
        image_id = getattr(deployment.image, "image_digest", "") or self.resolve_image(image_ref=deployment.image.image_ref)
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
            raise exceptions.ValidationError("Agent image must resolve to an immutable SHA-256 identity.")
        self._ensure_runtime_image(deployment=deployment, image_id=image_id)
        verify_admission(image_id)
        name = container_name(deployment)
        network = name + "-net"
        labels = {"nexus.managed": "agent", "nexus.agent.runtime": str(deployment.id), "nexus.agent.host": host_id()}
        environment = {
            "NEXUS_TENANT_ID": str(deployment.tenant_id),
            "NEXUS_PROJECT_ID": str(deployment.project_id or ""),
            "NEXUS_AGENT_ID": str(deployment.agent_id),
            "NEXUS_AGENT_VERSION": deployment.image.version.version if deployment.image.version else "",
        }
        from .python_builds import runtime_secrets
        # Real deployments always carry a persisted image. Keeping the lookup
        # conditional makes the Docker policy contract independently testable.
        if getattr(deployment.image, "pk", None):
            environment.update(runtime_secrets(deployment.image))
        if display_context is not None:
            if display_context.cloud_trust:
                environment["NEXUS_HOSTED_CLOUD_TRUST"] = json.dumps(display_context.cloud_trust, separators=(",", ":"))
            environment.update(
                {
                    "NEXUS_AGUI_RUN_ID": display_context.run_id,
                    "NEXUS_AGUI_EVENTS_URL": display_context.events_url,
                    "NEXUS_AGUI_TOKEN": display_context.write_token,
                    "NEXUS_DISPLAY_PUBLIC_URL": display_context.public_url,
                    "NEXUS_BROWSER_ENABLED": "true" if display_context.browser_enabled else "false",
                    "NEXUS_BROWSER_DELEGATE_URL": display_context.browser_delegate_url,
                    "NEXUS_BROWSER_DELEGATE_TOKEN": display_context.browser_delegate_token,
                    "NEXUS_BROWSER_COMPUTER_NAME": display_context.browser_computer_name,
                    "NEXUS_BROWSER_CAPTURE_EVENT_NAME": "nexus.computer.frame",
                    "NEXUS_BROWSER_CAPTURE_INTERVAL_SECONDS": str(getattr(settings, "NEXUS_AGENT_BROWSER_CAPTURE_INTERVAL_SECONDS", "1.0")),
                    "NEXUS_BROWSER_CAPTURE_MAX_IMAGE_BYTES": str(getattr(settings, "NEXUS_AGENT_BROWSER_CAPTURE_MAX_IMAGE_BYTES", 900_000)),
                }
            )
        if workspace_context is not None:
            environment.update(
                {
                    "NEXUS_WORKSPACE_CONNECTION_ID": workspace_context.connection_id,
                    "NEXUS_WORKSPACE_ROOT": workspace_context.root,
                    "NEXUS_WORKSPACE_ACCESS_MODE": workspace_context.access_mode,
                    "NEXUS_WORKSPACE_API_URL": workspace_context.api_url,
                    "NEXUS_WORKSPACE_TOKEN": workspace_context.token,
                }
            )
        port = int(getattr(settings, "NEXUS_AGENT_RUNTIME_CONTAINER_PORT", 8000))
        command = [
            "docker",
            "run",
            "-d",
            "--name", name,
            "--restart", "unless-stopped",
            "--read-only",
            "--tmpfs",
            str(getattr(settings, "NEXUS_AGENT_RUNTIME_TMPFS_SPEC", "/tmp:rw,nosuid,nodev,size=256m")),
            "--shm-size",
            str(getattr(settings, "NEXUS_AGENT_RUNTIME_SHM_SIZE", "256m")),
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            str(limits["memory_bytes"]),
            "--memory-swap", str(limits["memory_bytes"]),
            "--cpus", limits["cpu"],
            "--log-driver", "local",
            "--log-opt", "max-size=10m",
            "--log-opt", "max-file=3",
            "--pids-limit",
            str(getattr(settings, "NEXUS_AGENT_RUNTIME_PIDS_LIMIT", 256)),
            "--network",
            network,
        ]
        controller_container = str(
            getattr(settings, "NEXUS_AGENT_RUNTIME_CONTROLLER_CONTAINER", "") or ""
        ).strip()
        if not controller_container:
            command.extend(["-p", f"127.0.0.1:0:{port}"])
        runtime_user = str(getattr(settings, "NEXUS_AGENT_RUNTIME_USER", "65532:65532") or "65532:65532")
        if not re.fullmatch(r"[1-9]\d*(?::[1-9]\d*)?", runtime_user):
            raise exceptions.ValidationError("Agent runtime must use a non-root numeric UID/GID.")
        command.extend(["--user", runtime_user])
        for key, value in labels.items():
            command.extend(["--label", f"{key}={value}"])
        for key, value in environment.items():
            command.extend(["-e", key])
        command.append(image_id)
        from .network_policy import selected_policy
        network_policy = selected_policy()
        network_options = []
        if network_policy:
            network_options = network_policy.prepare(network, labels)
            # Docker must not restart workloads before the controller restores
            # their firewall after daemon/host restart. Legacy mode is unchanged.
            command[command.index('--restart') + 1] = 'no'
        existing = _docker_inspect("container", name)
        if existing:
            _check_docker_labels(existing, labels)
            if existing.get("Image") != image_id:
                raise exceptions.APIException("Docker generation image mismatch; refusing to adopt container.")
            if network_policy:
                network_policy.validate_container(existing, network)
        else:
            # Docker's HostPort=0 may change on restart. Persist an explicit
            # loopback port in the container configuration instead. If another
            # process wins the short allocation race, Docker fails closed.
            if not controller_container:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as port_socket:
                    port_socket.bind(("127.0.0.1", 0))
                    host_port = port_socket.getsockname()[1]
                command[command.index("-p") + 1] = f"127.0.0.1:{host_port}:{port}"
        network_info = _docker_inspect("network", network)
        created_network = not network_info
        if network_info:
            _check_docker_labels(network_info, labels)
        else:
            net_command = ["docker", "network", "create", "--driver", "bridge"]
            # Local mode publishes a loopback port and needs no peer on the
            # per-Agent network. Controller mode has exactly two members: the
            # Agent and its authenticated proxy, so peer traffic must work.
            if not controller_container:
                net_command.extend(["--opt", "com.docker.network.bridge.enable_icc=false"])
            for key, value in labels.items():
                net_command.extend(["--label", f"{key}={value}"])
            net_command.extend(network_options)
            _run_docker([*net_command, network])
        if network_policy:
            try:
                network_policy.validate_network(_docker_inspect('network', network), network, labels)
            except Exception:
                if created_network and not existing:
                    _docker_remove_network(network, labels)
                raise
        if controller_container:
            _docker_connect_controller(network, controller_container)
        container_id = ""
        try:
            if existing:
                container_id = existing["Id"]
                if not existing.get("State", {}).get("Running"):
                    _run_docker(["docker", "start", container_id])
            else:
                # Use the deterministic name for cleanup even if run timed out after creation.
                container_id = name
                container_id = _run_docker(command, environment=environment)
            if network_policy:
                network_policy.validate_container(_docker_inspect('container', container_id), network)
            if controller_container:
                internal_mcp_url = f"http://{name}:{port}/mcp"
            else:
                host_port = _docker_host_port(container_id=container_id, container_port=port)
                internal_mcp_url = f"http://127.0.0.1:{host_port}/mcp"
            if not _wait_for_mcp(url=internal_mcp_url):
                raise exceptions.APIException("Docker agent MCP endpoint did not become ready.")
            return RuntimeStartResult(container_id=container_id, internal_mcp_url=internal_mcp_url)
        except Exception as exc:
            diagnostics = _docker_runtime_diagnostics(container_id) if container_id else {}
            if container_id:
                _docker_stop(container_id, expected=labels)
            _docker_remove_network(network, labels)
            raise AgentDockerRuntimeError(diagnostics=diagnostics) from exc

    def _ensure_runtime_image(self, *, deployment: AgentRuntimeDeployment, image_id: str) -> None:
        """Materialize an immutable image after Docker host replacement.

        Uploaded and Python-built images are restored from shared storage. A
        registry image may be pulled again, but its digest must remain exactly
        the one that was admitted when the deployment was created.
        """

        # Small policy unit tests use an unpersisted image-like object. Every
        # production deployment references an AgentRuntimeImage row.
        if getattr(deployment.image, "pk", None) is None:
            return
        if _docker_inspect("image", image_id) is not None:
            return
        artifact_path = str(getattr(deployment.image, "artifact_path", "") or "").strip()
        if artifact_path:
            root = Path(settings.NEXUS_AGENT_STORAGE_ROOT).resolve()
            candidate = (root / artifact_path).resolve()
            try:
                candidate.relative_to(root)
            except ValueError as exc:
                raise exceptions.ValidationError("Agent image artifact is outside shared storage.") from exc
            if not candidate.is_file():
                raise exceptions.ValidationError("Agent image artifact is unavailable on this Docker worker.")
            self.load_image(artifact_path=str(candidate), image_ref=deployment.image.image_ref)
            restored = _docker_inspect("image", image_id)
            if restored is None:
                restored_by_ref = _docker_inspect("image", deployment.image.image_ref)
                if not restored_by_ref or restored_by_ref.get("Id") != image_id:
                    raise exceptions.ValidationError("Restored Agent image digest does not match its admitted identity.")
            return
        resolved = self.resolve_image(image_ref=deployment.image.image_ref)
        if resolved != image_id:
            raise exceptions.ValidationError("Agent image digest changed while recovering this Docker worker.")

    def stop(self, *, deployment: AgentRuntimeDeployment) -> None:  # pragma: no cover - integration surface
        from .docker_policy import assert_host, host_id
        # Revoked admission/egress configuration must never prevent emergency stop.
        assert_host(deployment, starting=False)
        if deployment.container_id:
            expected = {"nexus.managed": "agent", "nexus.agent.runtime": str(deployment.id), "nexus.agent.host": host_id()}
            _docker_stop(deployment.container_id, expected=expected)
            if deployment.container_id.startswith("nexus-agent-"):
                _docker_remove_network(deployment.container_id + "-net", expected)

    def health_check(self, *, deployment: AgentRuntimeDeployment) -> bool:  # pragma: no cover - integration surface
        if not deployment.container_id:
            return False
        try:
            from .docker_policy import container_name
            info = _docker_inspect("container", deployment.container_id)
            if not info or not info.get("State", {}).get("Running"):
                return False
            from .network_policy import selected_policy
            network_policy = selected_policy()
            if network_policy:
                from .docker_policy import host_id
                network = container_name(deployment) + '-net'
                labels = {'nexus.managed': 'agent', 'nexus.agent.runtime': str(deployment.id), 'nexus.agent.host': host_id()}
                _check_docker_labels(info, labels)
                network_policy.prepare(network, labels)
                network_policy.validate_container(info, network)
                network_policy.validate_network(_docker_inspect('network', network), network, labels)
            container_port = int(getattr(settings, "NEXUS_AGENT_RUNTIME_CONTAINER_PORT", 8000))
            endpoint = urlsplit(deployment.internal_mcp_url)
            controller_container = str(
                getattr(settings, "NEXUS_AGENT_RUNTIME_CONTROLLER_CONTAINER", "") or ""
            ).strip()
            if controller_container:
                expected_name = container_name(deployment)
                if endpoint.hostname != expected_name or endpoint.port != container_port:
                    return False
                network = expected_name + "-net"
                if network not in (info.get("NetworkSettings", {}).get("Networks") or {}):
                    return False
            else:
                addresses = info.get("NetworkSettings", {}).get("Ports", {}).get(f"{container_port}/tcp") or []
                if endpoint.hostname != "127.0.0.1" or not any(
                    address.get("HostIp") == "127.0.0.1" and address.get("HostPort") == str(endpoint.port)
                    for address in addresses
                ):
                    return False
        except exceptions.APIException:
            return False
        return _wait_for_mcp(url=deployment.internal_mcp_url, attempts=1)

    def diagnostics(self, *, deployment: AgentRuntimeDeployment) -> dict[str, Any]:
        return _docker_runtime_diagnostics(deployment.container_id)

    def sweep_orphans(self, *, valid_runtime_ids: set[str]) -> dict[str, int]:
        return _sweep_orphaned_agent_objects(valid_runtime_ids=valid_runtime_ids)

    def call_mcp(self, *, deployment: AgentRuntimeDeployment, method: str, headers: dict[str, str], body: bytes, path: str = "", timeout: int | float | None = None) -> RuntimeMCPResult:
        if not deployment.internal_mcp_url:
            raise exceptions.APIException("Docker MCP endpoint is not available.")
        return _http_request(
            url=mcp_transport_url(deployment.internal_mcp_url, path),
            method=method,
            headers=headers,
            body=body,
            timeout=30 if timeout is None else timeout,
        )

    def stream_mcp(self, *, deployment: AgentRuntimeDeployment, method: str, headers: dict[str, str], body: bytes, path: str = "") -> RuntimeMCPStreamResult:
        if not deployment.internal_mcp_url:
            raise exceptions.APIException("Docker MCP endpoint is not available.")
        return _http_stream_request(url=mcp_transport_url(deployment.internal_mcp_url, path), method=method,
            headers=headers, body=body, timeout=float(getattr(settings, "NEXUS_AGENT_STREAM_IDLE_TIMEOUT_SECONDS", 120)))


class _PinnedIPv6HTTPSConnection(HTTPSConnection):
    def __init__(
        self,
        *,
        address: str,
        port: int,
        server_hostname: str,
        context: ssl.SSLContext,
        timeout: float,
        source_address: str = "",
    ):
        super().__init__(server_hostname, port=port, timeout=timeout, context=context)
        self._edge_address = address
        self._edge_server_hostname = server_hostname
        self._edge_source_address = source_address

    def connect(self) -> None:
        connect_kwargs: dict[str, Any] = {}
        if self._edge_source_address:
            connect_kwargs["source_address"] = (self._edge_source_address, 0)
        raw_socket = socket.create_connection(
            (self._edge_address, self.port),
            self.timeout,
            **connect_kwargs,
        )
        try:
            self.sock = self._context.wrap_socket(raw_socket, server_hostname=self._edge_server_hostname)
        except Exception:
            raw_socket.close()
            raise


class OpenWrtIPv6RuntimeRunner(BaseAgentRuntimeRunner):
    def load_image(self, *, artifact_path: str, image_ref: str = "") -> str:
        raise exceptions.ValidationError("OpenWrt IPv6 runtimes do not use Docker images.")

    def validate_image_ref(self, *, image_ref: str) -> None:
        raise exceptions.ValidationError("OpenWrt IPv6 runtimes do not use Docker images.")

    def start(
        self,
        *,
        deployment: AgentRuntimeDeployment,
        display_context: RuntimeDisplayContext | None = None,
        workspace_context: RuntimeWorkspaceContext | None = None,
    ) -> RuntimeStartResult:
        registration = self._registration(deployment)
        return RuntimeStartResult(container_id="", internal_mcp_url=registration.endpoint_url)

    def stop(self, *, deployment: AgentRuntimeDeployment) -> None:
        return None

    def health_check(self, *, deployment: AgentRuntimeDeployment) -> bool:
        body = _json_dumps({"jsonrpc": "2.0", "id": "nexus-health", "method": "initialize", "params": {}})
        try:
            result = self.call_mcp(
                deployment=deployment,
                method="POST",
                headers={"Content-Type": "application/json", "MCP-Protocol-Version": "2025-06-18"},
                body=body,
            )
        except exceptions.APIException:
            return False
        return 200 <= result.status_code < 400

    def call_mcp(self, *, deployment: AgentRuntimeDeployment, method: str, headers: dict[str, str], body: bytes, path: str = "", timeout: int | float | None = None) -> RuntimeMCPResult:
        if path:
            raise AgentRuntimeStreamingUnsupported("Legacy SSE is only available for Docker Agent runtimes.")
        connection, request_path, outbound_headers = self._connection(
            deployment=deployment,
            headers=headers,
            body=body,
            timeout=timeout,
        )
        try:
            connection.request(method.upper(), request_path, body=body if method.upper() not in {"GET", "HEAD"} else None, headers=outbound_headers)
            response = connection.getresponse()
            return RuntimeMCPResult(
                status_code=response.status,
                headers={key: value for key, value in response.getheaders()},
                body=response.read(),
            )
        except (OSError, ssl.SSLError, SocketTimeout, TimeoutError, HTTPException) as exc:
            raise AgentRuntimeConnectionFailed() from exc
        finally:
            connection.close()

    def stream_mcp(self, *, deployment: AgentRuntimeDeployment, method: str, headers: dict[str, str], body: bytes, path: str = "") -> RuntimeMCPStreamResult:
        if path:
            raise AgentRuntimeStreamingUnsupported("Legacy SSE is only available for Docker Agent runtimes.")
        connection, request_path, outbound_headers = self._connection(deployment=deployment, headers=headers, body=body,
            timeout=float(getattr(settings, "NEXUS_AGENT_STREAM_IDLE_TIMEOUT_SECONDS", 120)))
        outbound_headers["Accept"] = "text/event-stream"
        try:
            connection.request(method.upper(), request_path, body=body if method.upper() not in {"GET", "HEAD"} else None, headers=outbound_headers)
            response = connection.getresponse()
        except (OSError, ssl.SSLError, SocketTimeout, TimeoutError, RemoteDisconnected, BadStatusLine) as exc:
            connection.close()
            raise AgentRuntimeConnectionFailed() from exc

        def chunks():
            try:
                while True:
                    chunk = response.read1(8192)
                    if not chunk:
                        break
                    yield chunk
            finally:
                connection.close()

        return RuntimeMCPStreamResult(
            status_code=response.status,
            headers={key: value for key, value in response.getheaders()},
            chunks=chunks(),
        )

    def _registration(self, deployment: AgentRuntimeDeployment):
        registration = deployment.edge_registration
        if registration is None or registration.status != "active":
            raise exceptions.APIException("OpenWrt Agent registration is unavailable.")
        if registration.lease_expires_at <= timezone.now():
            raise exceptions.APIException("OpenWrt Agent registration lease has expired.")
        expected_path = f"/mcp/{quote(registration.origin, safe='-._~')}"
        if registration.path != expected_path:
            raise exceptions.APIException("OpenWrt Agent MCP path does not match its registered origin.")
        return registration

    def _connection(
        self,
        *,
        deployment: AgentRuntimeDeployment,
        headers: dict[str, str],
        body: bytes,
        timeout: int | float | None = None,
    ):
        from .edge_auth import issue_edge_access_token
        from .runtime_context import prepare_openwrt_run_context_headers

        registration = self._registration(deployment)
        context = self._ssl_context(registration.ca_bundle_id)
        request_timeout = float(
            getattr(settings, "NEXUS_EDGE_REQUEST_TIMEOUT_SECONDS", 30)
            if timeout is None
            else timeout
        )
        request_timeout = min(max(request_timeout, 0.1), 86400.0)
        connection = _PinnedIPv6HTTPSConnection(
            address=registration.ipv6_address,
            port=registration.port,
            server_hostname=registration.tls_server_name,
            context=context,
            timeout=request_timeout,
            source_address=str(
                getattr(settings, "NEXUS_EDGE_IPV6_SOURCE_ADDRESS", "") or ""
            ).strip(),
        )
        exchanged_headers = prepare_openwrt_run_context_headers(
            deployment=deployment,
            headers=headers,
        )
        outbound_headers = _allowed_mcp_headers(headers=exchanged_headers, body=body)
        outbound_headers["Authorization"] = f"Bearer {issue_edge_access_token(deployment=deployment)}"
        outbound_headers["X-Nexus-Target-Agent"] = registration.origin
        outbound_headers["X-Nexus-Deadline"] = (
            timezone.now() + timedelta(seconds=request_timeout)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        return connection, registration.path, outbound_headers

    def _ssl_context(self, ca_bundle_id: str) -> ssl.SSLContext:
        bundles = getattr(settings, "NEXUS_EDGE_CA_BUNDLES", {}) or {}
        ca_file = str(bundles.get(ca_bundle_id) or getattr(settings, "NEXUS_EDGE_CA_FILE", "") or "")
        context = ssl.create_default_context(cafile=ca_file or None)
        cert_file = str(getattr(settings, "NEXUS_EDGE_CLIENT_CERT_FILE", "") or "")
        key_file = str(getattr(settings, "NEXUS_EDGE_CLIENT_KEY_FILE", "") or "")
        if cert_file and key_file:
            context.load_cert_chain(certfile=cert_file, keyfile=key_file)
        elif not getattr(settings, "NEXUS_EDGE_ALLOW_WITHOUT_MTLS", False):
            raise exceptions.APIException("Nexus Edge mTLS client certificate is not configured.")
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        return context


class OpenWrtRelayRuntimeRunner(BaseAgentRuntimeRunner):
    def load_image(self, *, artifact_path: str, image_ref: str = "") -> str:
        raise exceptions.ValidationError("OpenWrt Relay runtimes do not use Docker images.")

    def validate_image_ref(self, *, image_ref: str) -> None:
        raise exceptions.ValidationError("OpenWrt Relay runtimes do not use Docker images.")

    def start(
        self,
        *,
        deployment: AgentRuntimeDeployment,
        display_context: RuntimeDisplayContext | None = None,
        workspace_context: RuntimeWorkspaceContext | None = None,
    ) -> RuntimeStartResult:
        registration = self._registration(deployment)
        return RuntimeStartResult(container_id="", internal_mcp_url=registration.endpoint_url)

    def stop(self, *, deployment: AgentRuntimeDeployment) -> None:
        return None

    def health_check(self, *, deployment: AgentRuntimeDeployment) -> bool:
        try:
            self._registration(deployment)
        except exceptions.APIException:
            return False
        return True

    def call_mcp(self, *, deployment: AgentRuntimeDeployment, method: str, headers: dict[str, str], body: bytes, path: str = "", timeout: int | float | None = None) -> RuntimeMCPResult:
        if path:
            return _relay_mcp_error(None, -32601, "Legacy SSE is not supported by Relay Agents.", status=405)
        if method.upper() != "POST":
            return _relay_mcp_error(None, -32601, "Relay MCP supports POST requests only.", status=405)
        try:
            payload = _json_loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError, exceptions.APIException):
            return _relay_mcp_error(None, -32700, "Parse error")
        request_id = payload.get("id")
        rpc_method = payload.get("method")
        registration = self._registration(deployment)
        if rpc_method == "initialize":
            return _relay_mcp_result(
                request_id,
                {
                    "protocolVersion": headers.get("MCP-Protocol-Version") or headers.get("mcp-protocol-version") or "2025-06-18",
                    "serverInfo": {"name": deployment.agent.name, "version": deployment.agent.current_version or ""},
                    "capabilities": {"tools": {}},
                },
            )
        if rpc_method == "notifications/initialized":
            return RuntimeMCPResult(status_code=202, headers={"Content-Type": "application/json"}, body=b"")
        if rpc_method == "tools/list":
            tools = []
            for mapping in registration.mcp_tools:
                tool = {
                    "name": mapping["name"],
                    "description": mapping.get("description") or mapping["intent"],
                    "inputSchema": mapping.get("input_schema") or {"type": "object", "additionalProperties": True},
                }
                if mapping.get("title"):
                    tool["title"] = mapping["title"]
                tools.append(tool)
            return _relay_mcp_result(request_id, {"tools": tools})
        if rpc_method != "tools/call":
            return _relay_mcp_error(request_id, -32601, "Method not found")

        params = payload.get("params")
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return _relay_mcp_error(request_id, -32602, "Invalid tools/call params")
        mapping = next((item for item in registration.mcp_tools if item.get("name") == params["name"]), None)
        if mapping is None:
            return _relay_mcp_error(request_id, -32004, "Unknown Agent tool", status=404)
        if not isinstance(request_id, (str, int)) or isinstance(request_id, bool):
            return _relay_mcp_error(request_id, -32600, "A string or integer JSON-RPC id is required")
        task_id = f"mcp:{request_id}"
        if len(task_id.encode("ascii", "ignore")) != len(task_id) or len(task_id) >= 65:
            return _relay_mcp_error(request_id, -32600, "JSON-RPC id exceeds the Relay task bound")
        envelope = {
            "version": "1.0",
            "intent": mapping["intent"],
            "intent_version": int(mapping.get("intent_version") or 1),
            "task_id": task_id,
            "tenant": str(deployment.tenant_id),
            "source_agent": "service://nexus-server",
            "target_agent": registration.origin,
            "hop_limit": 8,
            "flags": {"idempotent": False, "allow_retry": False},
            "constraints": {"region": "*"},
            "payload": {
                "protocol": "mcp",
                "authority": registration.origin,
                "selector": mapping["name"],
                "request": payload,
            },
        }
        from .runtime_context import prepare_openwrt_run_context_headers

        exchanged_headers = prepare_openwrt_run_context_headers(
            deployment=deployment,
            headers=headers,
        )
        exchange_url = exchanged_headers.get("X-Nexus-Run-Context-Url", "")
        exchange_token = exchanged_headers.get("X-Nexus-Run-Context-Token", "")
        if exchange_url and exchange_token:
            # Relay v1 has no arbitrary HTTP header channel. The device gateway
            # forwards this safe, single-use reference in the Envelope and the
            # Agent SDK exchanges it for the Run-scoped trace/billing context.
            envelope["nexus_run_context"] = {
                "exchange_url": exchange_url,
                "exchange_token": exchange_token,
            }
        envelope_body = _json_dumps(envelope)
        from .relay_services import relay_invoke

        timeout = float(getattr(settings, "NEXUS_EDGE_REQUEST_TIMEOUT_SECONDS", 30))
        status_code, response_headers, response_body = relay_invoke(
            relay_id=registration.relay_id,
            target_router_id=registration.relay_router_id,
            target_agent=registration.origin,
            tenant=str(deployment.tenant_id),
            intent=mapping["intent"],
            task_id=task_id,
            body=envelope_body,
            timeout=timeout,
        )
        if status_code < 200 or status_code >= 300:
            return _relay_gateway_error(request_id, status_code, response_body)
        text_body = response_body.decode("utf-8", errors="replace")
        result: dict[str, Any] = {
            "content": [{"type": "text", "text": text_body}],
            "isError": False,
        }
        content_type = next((value for key, value in response_headers.items() if key.lower() == "content-type"), "")
        if "json" in content_type.lower() or text_body.lstrip().startswith(("{", "[")):
            try:
                structured = json.loads(text_body)
            except json.JSONDecodeError:
                structured = None
            if isinstance(structured, dict):
                result["structuredContent"] = structured
        return _relay_mcp_result(request_id, result)

    def stream_mcp(self, *, deployment: AgentRuntimeDeployment, method: str, headers: dict[str, str], body: bytes, path: str = "") -> RuntimeMCPStreamResult:
        raise AgentRuntimeStreamingUnsupported("OpenWrt Relay v1 does not support MCP streaming.")

    def _registration(self, deployment: AgentRuntimeDeployment):
        from .models import EdgeAgentRegistration

        registration = deployment.edge_registration
        if registration is None or registration.status != "active":
            raise exceptions.APIException("OpenWrt Relay Agent registration is unavailable.")
        if registration.transport != EdgeAgentRegistration.TRANSPORT_RELAY:
            raise exceptions.APIException("OpenWrt Agent registration is not a Relay transport.")
        now = timezone.now()
        if registration.lease_expires_at <= now:
            raise exceptions.APIException("OpenWrt Relay Agent registration lease has expired.")
        if not registration.node.relay_lease_expires_at or registration.node.relay_lease_expires_at <= now:
            raise exceptions.APIException("OpenWrt Relay assignment lease has expired.")
        if registration.relay_id != registration.node.relay_id or registration.relay_assignment_id != registration.node.relay_assignment_id:
            raise exceptions.APIException("OpenWrt Relay assignment has changed; wait for registration renewal.")
        return registration


def _relay_mcp_result(request_id, result: dict[str, Any]) -> RuntimeMCPResult:
    return RuntimeMCPResult(
        status_code=200,
        headers={"Content-Type": "application/json"},
        body=_json_dumps({"jsonrpc": "2.0", "result": result, "id": request_id}),
    )


def _relay_mcp_error(request_id, code: int, message: str, *, status: int = 400, data: Any = None) -> RuntimeMCPResult:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return RuntimeMCPResult(
        status_code=status,
        headers={"Content-Type": "application/json"},
        body=_json_dumps({"jsonrpc": "2.0", "error": error, "id": request_id}),
    )


def _relay_gateway_error(request_id, status_code: int, body: bytes) -> RuntimeMCPResult:
    code = -32000
    if status_code in {401, 403}:
        code = -32001
    elif status_code == 404:
        code = -32004
    elif status_code in {408, 504}:
        code = -32008
    elif status_code == 429:
        code = -32029
    text_body = body.decode("utf-8", errors="replace")
    try:
        gateway_body: Any = json.loads(text_body)
    except json.JSONDecodeError:
        gateway_body = text_body
    return _relay_mcp_error(
        request_id,
        code,
        "Nexus Agent invocation failed",
        status=status_code,
        data={"gatewayStatus": status_code, "gatewayBody": gateway_body},
    )


def _run_docker(command: list[str], timeout: int | float = 60, environment: dict | None = None) -> str:
    try:
        kwargs = {"env": {**os.environ, **environment}} if environment is not None else {}
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, **kwargs)
    except (OSError, subprocess.SubprocessError) as exc:
        raise exceptions.APIException("Docker operation unavailable or timed out; check worker diagnostics.") from exc
    if result.returncode != 0:
        raise exceptions.APIException("Docker operation failed; check worker diagnostics.")
    return result.stdout.strip()


def _docker_inspect(kind: str, identifier: str) -> dict | None:
    try:
        result = subprocess.run(["docker", kind, "inspect", identifier], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise exceptions.APIException("Docker inspection unavailable.") from exc
    if result.returncode:
        if re.search(r"no such (object|image|container|network)|not found", result.stderr or "", re.I):
            return None
        raise exceptions.APIException("Docker inspection failed; container absence could not be established.")
    try:
        value = json.loads(result.stdout)
        if len(value) == 1 and isinstance(value[0], dict):
            return value[0]
    except (TypeError, ValueError):
        pass
    raise exceptions.APIException("Docker returned an invalid inspection response.")


def _check_docker_labels(info, expected):
    labels = info.get("Config", {}).get("Labels") or info.get("Labels") or {}
    if any(labels.get(key) != value for key, value in expected.items()):
        raise exceptions.APIException("Docker object ownership mismatch; refusing to modify it.")


def _docker_remove_network(name, expected):
    info = _docker_inspect("network", name)
    if info:
        _check_docker_labels(info, expected)
        controller = str(
            getattr(settings, "NEXUS_AGENT_RUNTIME_CONTROLLER_CONTAINER", "") or ""
        ).strip()
        if controller:
            attached = {
                str(value.get("Name") or "")
                for value in (info.get("Containers") or {}).values()
                if isinstance(value, dict)
            }
            if controller in attached:
                controller_info = _docker_inspect("container", controller)
                controller_labels = (controller_info or {}).get("Config", {}).get("Labels") or {}
                if controller_labels.get("nexus.managed") != "agent-controller":
                    raise exceptions.APIException(
                        "Agent Runtime Controller ownership could not be verified."
                    )
                _run_docker(["docker", "network", "disconnect", "-f", name, controller])
        _run_docker(["docker", "network", "rm", name])


def _docker_connect_controller(network: str, controller: str) -> None:
    controller_info = _docker_inspect("container", controller)
    labels = (controller_info or {}).get("Config", {}).get("Labels") or {}
    if labels.get("nexus.managed") != "agent-controller":
        raise exceptions.APIException("Agent Runtime Controller container ownership could not be verified.")
    network_info = _docker_inspect("network", network)
    attached = {
        str(value.get("Name") or "")
        for value in (network_info or {}).get("Containers", {}).values()
        if isinstance(value, dict)
    }
    if controller not in attached:
        _run_docker(["docker", "network", "connect", network, controller])


def _docker_runtime_diagnostics(container_id: str) -> dict[str, Any]:
    if not container_id:
        return {"available": False}
    info = _docker_inspect("container", container_id)
    if info is None:
        return {"available": False}
    state = info.get("State") if isinstance(info.get("State"), dict) else {}
    result: dict[str, Any] = {
        "available": True,
        "status": str(state.get("Status") or "unknown")[:32],
        "exit_code": int(state.get("ExitCode") or 0),
        "oom_killed": bool(state.get("OOMKilled")),
        "restart_count": int(info.get("RestartCount") or 0),
        "started_at": str(state.get("StartedAt") or "")[:64],
        "finished_at": str(state.get("FinishedAt") or "")[:64],
    }
    tail = _docker_log_tail(container_id)
    if tail:
        result["log_tail"] = tail
    return result


def _docker_log_tail(container_id: str) -> str:
    try:
        result = subprocess.run(
            ["docker", "logs", "--tail", "80", container_id],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    text = "\n".join(value for value in (result.stdout, result.stderr) if value)
    text = text[-8192:]
    # Logs belong to the Agent owner, but common credentials still receive a
    # conservative second-line redaction before persistence.
    patterns = (
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s]+",
        r"(?i)((?:token|password|secret|api[_-]?key)\s*[:=]\s*)[^\s,;]+",
        r"\b(?:sk|sa-nexus|nexus)[-_][A-Za-z0-9._-]{12,}\b",
    )
    for pattern in patterns:
        text = re.sub(pattern, lambda match: match.group(1) + "[REDACTED]" if match.lastindex else "[REDACTED]", text)
    return text


def _old_enough(info: dict[str, Any], *, grace_seconds: int) -> bool:
    created = parse_datetime(str(info.get("Created") or ""))
    return bool(created and created <= timezone.now() - timedelta(seconds=grace_seconds))


def _sweep_orphaned_agent_objects(*, valid_runtime_ids: set[str]) -> dict[str, int]:
    from .docker_policy import host_id

    grace = max(300, int(getattr(settings, "NEXUS_AGENT_RUNTIME_ORPHAN_GRACE_SECONDS", 3600)))
    removed_containers = 0
    removed_networks = 0
    container_ids = _run_docker(
        ["docker", "ps", "-a", "--filter", "label=nexus.managed=agent", "-q"]
    ).splitlines()
    for identifier in container_ids:
        info = _docker_inspect("container", identifier)
        labels = (info or {}).get("Config", {}).get("Labels") or {}
        runtime_id = str(labels.get("nexus.agent.runtime") or "")
        if (
            labels.get("nexus.managed") != "agent"
            or labels.get("nexus.agent.host") != host_id()
            or runtime_id in valid_runtime_ids
            or not info
            or not _old_enough(info, grace_seconds=grace)
        ):
            continue
        expected = {
            "nexus.managed": "agent",
            "nexus.agent.runtime": runtime_id,
            "nexus.agent.host": host_id(),
        }
        _docker_stop(str(info.get("Id") or identifier), expected=expected)
        removed_containers += 1

    network_ids = _run_docker(
        ["docker", "network", "ls", "--filter", "label=nexus.managed=agent", "-q"]
    ).splitlines()
    for identifier in network_ids:
        info = _docker_inspect("network", identifier)
        labels = (info or {}).get("Labels") or {}
        runtime_id = str(labels.get("nexus.agent.runtime") or "")
        if (
            labels.get("nexus.managed") != "agent"
            or labels.get("nexus.agent.host") != host_id()
            or runtime_id in valid_runtime_ids
            or not info
            or not _old_enough(info, grace_seconds=grace)
        ):
            continue
        _docker_remove_network(str(info.get("Name") or identifier), {
            "nexus.managed": "agent",
            "nexus.agent.runtime": runtime_id,
            "nexus.agent.host": host_id(),
        })
        removed_networks += 1
    return {"containers": removed_containers, "networks": removed_networks}


def _docker_image_available(*, image_ref: str) -> bool:
    try:
        if subprocess.run(["docker", "image", "inspect", image_ref], capture_output=True, text=True, timeout=30).returncode == 0:
            return True
        return subprocess.run(["docker", "manifest", "inspect", image_ref], capture_output=True, text=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _parse_docker_load_ref(output: str) -> str:
    for line in output.splitlines():
        text = line.strip()
        if text.startswith("Loaded image:"):
            return text.split("Loaded image:", 1)[1].strip()
        if text.startswith("Loaded image ID:"):
            return text.split("Loaded image ID:", 1)[1].strip()
    raise exceptions.APIException("Docker load did not report an image reference.")


def _docker_host_port(*, container_id: str, container_port: int) -> int:
    output = _run_docker(["docker", "port", container_id, f"{container_port}/tcp"])
    # Docker may return values like "0.0.0.0:49153" or "[::]:49153".
    try:
        first = output.splitlines()[0].strip()
        return int(first.rsplit(":", 1)[1])
    except (IndexError, ValueError) as exc:
        raise exceptions.APIException("Docker did not publish the agent MCP port.") from exc


def _docker_stop(container_id: str, expected: dict | None = None) -> None:
    info = _docker_inspect("container", container_id)
    if info is None:
        return
    labels = info.get("Config", {}).get("Labels") or {}
    if expected and (labels.get("nexus.managed") or not re.fullmatch(r"[a-f0-9]{64}", container_id)):
        _check_docker_labels(info, expected)
    # Disable daemon restart before stopping. Failed removal is retryable and visible.
    _run_docker(["docker", "update", "--restart=no", container_id])
    _run_docker(["docker", "stop", "--time", "20", container_id], timeout=35)
    _run_docker(["docker", "rm", container_id])
    if labels.get("nexus.managed") == "agent":
        expected = {key: labels[key] for key in ("nexus.managed", "nexus.agent.runtime", "nexus.agent.host") if key in labels}
        for name in info.get("NetworkSettings", {}).get("Networks", {}):
            if name.startswith("nexus-agent-") and name.endswith("-net"):
                _docker_remove_network(name, expected)


def validate_docker_image_reference(image_ref: str) -> None:
    value = str(image_ref or "").strip()
    if not value:
        raise exceptions.ValidationError("Docker image is required.")
    if len(value) > 512:
        raise exceptions.ValidationError("Docker image reference is too long.")
    if value.startswith(("http://", "https://")):
        raise exceptions.ValidationError("Docker image must be an image reference, not a URL.")
    if re.search(r"\s|[;&|`$<>]", value):
        raise exceptions.ValidationError("Docker image reference contains invalid characters.")

    name_part = value.split("@", 1)[0]
    path_part = name_part.rsplit(":", 1)[0] if ":" in name_part.rsplit("/", 1)[-1] else name_part
    tag_part = name_part.rsplit(":", 1)[1] if ":" in name_part.rsplit("/", 1)[-1] else ""
    digest_part = value.split("@", 1)[1] if "@" in value else ""

    parts = path_part.split("/")
    component_pattern = r"[a-z0-9]+(?:(?:[._-]|__)[a-z0-9]+)*"
    registry_pattern = rf"{component_pattern}(?::[0-9]+)?"
    if not parts or not all(re.fullmatch(component_pattern, part) for part in parts[1:]):
        raise exceptions.ValidationError("Docker image name must use lowercase repository components.")
    if "/" in path_part and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        first_component_valid = re.fullmatch(registry_pattern, parts[0]) is not None
    else:
        first_component_valid = re.fullmatch(component_pattern, parts[0]) is not None
    if not first_component_valid:
        raise exceptions.ValidationError("Docker image name must use lowercase repository components.")
    if tag_part and not re.fullmatch(r"[\w][\w.-]{0,127}", tag_part):
        raise exceptions.ValidationError("Docker image tag is invalid.")
    if digest_part and not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*:[0-9A-Fa-f]{32,}", digest_part):
        raise exceptions.ValidationError("Docker image digest is invalid.")
    if not tag_part and not digest_part:
        raise exceptions.ValidationError("Docker image must include an explicit tag or digest, for example registry.example.com/team/agent:v1.")


def _wait_for_mcp(*, url: str, attempts: int | None = None) -> bool:
    max_attempts = attempts or int(getattr(settings, "NEXUS_AGENT_RUNTIME_START_ATTEMPTS", 30))
    delay = float(getattr(settings, "NEXUS_AGENT_RUNTIME_START_DELAY_SECONDS", 0.25))
    protocol_version = "2025-06-18"
    accept = "application/json, text/event-stream"
    body = _json_dumps(
        {
            "jsonrpc": "2.0",
            "id": "health",
            "method": "initialize",
            "params": {
                "protocolVersion": protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "nexus-runtime-health", "version": "1.0"},
            },
        }
    )
    for _ in range(max_attempts):
        try:
            response = _http_request(
                url=url,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Accept": accept,
                    "MCP-Protocol-Version": protocol_version,
                },
                body=body,
                timeout=2,
                max_response_bytes=65536,
                follow_redirects=False,
            )
            if 200 <= response.status_code < 300:
                session_id = next(
                    (value for key, value in response.headers.items() if key.lower() == "mcp-session-id"),
                    "",
                )
                if session_id:
                    try:
                        _http_request(
                            url=url,
                            method="DELETE",
                            headers={
                                "Accept": accept,
                                "MCP-Protocol-Version": protocol_version,
                                "Mcp-Session-Id": session_id,
                            },
                            body=b"",
                            timeout=2,
                            max_response_bytes=65536,
                            follow_redirects=False,
                        )
                    except exceptions.APIException:
                        pass
                if _valid_mcp_initialize(response.body):
                    return True
        except exceptions.APIException:
            pass
        time.sleep(delay)
    return False


def _valid_mcp_initialize(body: bytes) -> bool:
    if len(body) > 65536:
        return False
    try:
        text = body.decode("utf-8")
        candidates = [text]
        if text.lstrip().startswith(("event:", "data:", ":")):
            candidates = ["\n".join(line[5:].lstrip() for line in block.splitlines() if line.startswith("data:"))
                          for block in text.replace("\r\n", "\n").split("\n\n")]
        for candidate in candidates:
            try:
                value = json.loads(candidate)
            except ValueError:
                continue
            if not isinstance(value, dict) or value.get("jsonrpc") != "2.0" or value.get("id") != "health" or "error" in value:
                continue
            result = value.get("result")
            if (isinstance(result, dict) and result.get("protocolVersion") in {"2024-11-05", "2025-03-26", "2025-06-18"}
                    and isinstance(result.get("capabilities"), dict) and isinstance(result.get("serverInfo"), dict)
                    and isinstance(result["serverInfo"].get("name"), str) and result["serverInfo"]["name"]
                    and isinstance(result["serverInfo"].get("version"), str)):
                return True
    except (UnicodeError, TypeError):
        pass
    return False


def mcp_transport_url(url: str, path: str = "") -> str:
    if not path:
        return url
    requested = urlsplit(path)
    if requested.path not in {"/mcp", "/sse", "/messages/"}:
        raise exceptions.APIException("Unsupported MCP transport path.")
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, requested.path, requested.query, ""))


_MCP_REQUEST_HEADERS = {
    "accept",
    "content-type",
    "last-event-id",
    "mcp-protocol-version",
    "mcp-method",
    "mcp-name",
    "mcp-session-id",
    # These values are created by Nexus after client-provided values with the
    # same prefixes have been removed in runtime_headers_with_agui().
    "x-nexus-agui-events-url",
    "x-nexus-agui-run-id",
    "x-nexus-agui-token",
    "x-nexus-browser-computer-name",
    "x-nexus-browser-delegate-token",
    "x-nexus-browser-delegate-url",
    "x-nexus-browser-enabled",
    "x-nexus-computer-enabled",
    "x-nexus-memory-url",
    "x-nexus-output-root",
    "x-nexus-interaction-url",
    "x-nexus-interaction-token",
    "x-nexus-interaction-mode",
    "x-nexus-checkpoint-url",
    "x-nexus-display-asset-url",
    "x-nexus-mobile-capabilities",
    "x-nexus-mobile-delegate-token",
    "x-nexus-mobile-delegate-url",
    "x-nexus-mobile-enabled",
    "x-nexus-run-messages-url",
    "x-nexus-run-turn",
    "x-nexus-terminal-url",
    "x-nexus-workspace-capabilities",
    "x-nexus-workspace-delegate-token",
    "x-nexus-workspace-delegate-url",
    "x-nexus-workspace-root",
    "x-nexus-workspace-token",
    "x-nexus-workspace-url",
    "x-nexus-run-context-url",
    "x-nexus-run-context-token",
    "x-nexus-billing-url",
    "x-nexus-billing-token",
    "x-nexus-billing-currency",
    "x-nexus-billing-max-cost",
}


class _NoRuntimeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_request(
    *,
    url: str,
    method: str,
    headers: dict[str, str],
    body: bytes,
    timeout: int | float = 30,
    max_response_bytes: int | None = None,
    follow_redirects: bool = True,
) -> RuntimeMCPResult:
    outbound_headers = {
        key: value
        for key, value in headers.items()
        if key.lower() in _MCP_REQUEST_HEADERS and value
    }
    if body and "Content-Type" not in outbound_headers and "content-type" not in {key.lower() for key in outbound_headers}:
        outbound_headers["Content-Type"] = "application/json"
    request = Request(
        url,
        data=body if method.upper() not in {"GET", "HEAD"} else None,
        headers=outbound_headers,
        method=method.upper(),
    )
    try:
        open_request = urlopen if follow_redirects else build_opener(_NoRuntimeRedirect()).open
        with open_request(request, timeout=timeout) as response:
            return RuntimeMCPResult(
                status_code=response.status,
                headers={key: value for key, value in response.headers.items()},
                body=response.read() if max_response_bytes is None else response.read(max_response_bytes + 1),
            )
    except HTTPError as exc:
        return RuntimeMCPResult(
            status_code=exc.code,
            headers={key: value for key, value in exc.headers.items()},
            body=exc.read() if max_response_bytes is None else exc.read(max_response_bytes + 1),
        )
    except (URLError, OSError, SocketTimeout, TimeoutError, RemoteDisconnected, BadStatusLine) as exc:
        raise AgentRuntimeConnectionFailed() from exc


def _http_stream_request(
    *,
    url: str,
    method: str,
    headers: dict[str, str],
    body: bytes,
    timeout: int | float = 30,
) -> RuntimeMCPStreamResult:
    outbound_headers = _allowed_mcp_headers(headers=headers, body=body)
    if not any(key.lower() == "accept" for key in outbound_headers):
        outbound_headers["Accept"] = "text/event-stream"
    request = Request(
        url,
        data=body if method.upper() not in {"GET", "HEAD"} else None,
        headers=outbound_headers,
        method=method.upper(),
    )
    try:
        response = urlopen(request, timeout=timeout)
    except HTTPError as exc:
        return RuntimeMCPStreamResult(
            status_code=exc.code,
            headers={key: value for key, value in exc.headers.items()},
            chunks=iter([exc.read()]),
        )
    except (URLError, OSError, SocketTimeout, TimeoutError, RemoteDisconnected, BadStatusLine) as exc:
        raise AgentRuntimeConnectionFailed() from exc

    def chunks():
        try:
            while True:
                chunk = response.read1(8192)
                if not chunk:
                    break
                yield chunk
        finally:
            response.close()

    return RuntimeMCPStreamResult(
        status_code=response.status,
        headers={key: value for key, value in response.headers.items()},
        chunks=chunks(),
    )


def _allowed_mcp_headers(*, headers: dict[str, str], body: bytes) -> dict[str, str]:
    outbound_headers = {
        key: value
        for key, value in headers.items()
        if key.lower() in _MCP_REQUEST_HEADERS and value
    }
    content_type_key = next(
        (key for key in outbound_headers if key.lower() == "content-type"),
        "",
    )
    if body and not content_type_key:
        outbound_headers["Content-Type"] = "application/json; charset=utf-8"
    elif content_type_key:
        content_type = outbound_headers[content_type_key]
        if content_type.lower().startswith("application/json") and "charset=" not in content_type.lower():
            outbound_headers[content_type_key] = f"{content_type}; charset=utf-8"
    return outbound_headers


def get_runtime_runner(deployment: AgentRuntimeDeployment | None = None) -> BaseAgentRuntimeRunner:
    if deployment is not None and deployment.runtime_kind == AgentRuntimeDeployment.RUNTIME_OPENWRT_IPV6:
        return OpenWrtIPv6RuntimeRunner()
    if deployment is not None and deployment.runtime_kind == AgentRuntimeDeployment.RUNTIME_OPENWRT_RELAY:
        return OpenWrtRelayRuntimeRunner()
    runner = getattr(settings, "NEXUS_AGENT_RUNTIME_RUNNER", "fake")
    if runner == "docker":
        return DockerAgentRuntimeRunner()
    if runner == "controller":
        from .runtime_controller import ControllerAgentRuntimeRunner
        return ControllerAgentRuntimeRunner()
    if runner == "fake" and not getattr(settings, "NEXUS_PRODUCTION", False):
        return FakeAgentRuntimeRunner()
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured("A real Docker Agent runner is required in production; fake is test-only.")


def _json_loads(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    value = json.loads(body.decode("utf-8"))
    return value if isinstance(value, dict) else {}


def _json_dumps(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
