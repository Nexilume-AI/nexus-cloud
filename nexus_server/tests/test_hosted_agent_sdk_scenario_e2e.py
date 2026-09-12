from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.test import LiveServerTestCase, SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework import exceptions

from apps.agents import runtime_runner
from apps.agents.models import (
    Agent,
    AgentComputerBinding,
    AgentDisplayRun,
    AgentMemoryItem,
    AgentOutputArtifact,
    AgentRuntimeDeployment,
    AgentRuntimeImage,
    AgentRuntimeInvocation,
    AgentVersion,
)
from apps.agents.runtime_services import recall_invocation_memory
from apps.datasets.storage_backends import get_dataset_storage_backend
from apps.common.subjects import hash_token, request_subject
from apps.tenancy.models import Membership, Project, Tenant
from apps.workspaces.models import WorkspaceConnection, WorkspaceTerminalSession


REPO_ROOT = Path(__file__).resolve().parents[2]
SDK_ROOT = REPO_ROOT / "nexus_openwrt" / "sdk" / "nexus-agent-sdk-python"
EXAMPLE_ROOT = SDK_ROOT / "examples" / "hosted_workspace_inspector"
WORKSPACE_SCOPES = ["files.list", "files.read", "files.write", "command.execute"]


def hosted_agent_e2e_enabled() -> bool:
    return os.environ.get("NEXUS_HOSTED_AGENT_E2E") == "1"


def run_checked(command: list[str], *, cwd: Path | None = None, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if result.returncode != 0:
        rendered = " ".join(command)
        raise AssertionError(f"Command failed ({rendered}):\n{result.stderr or result.stdout}")
    return result


class HostedWorkspaceInspectorExampleContractTests(SimpleTestCase):
    def test_example_uses_the_real_sdk_and_least_privilege_business_tool(self):
        source = (EXAMPLE_ROOT / "agent.py").read_text(encoding="utf-8")
        dockerfile = (EXAMPLE_ROOT / "Dockerfile").read_text(encoding="utf-8")
        requirements = (EXAMPLE_ROOT / "requirements.txt").read_text(encoding="utf-8")

        compile(source, str(EXAMPLE_ROOT / "agent.py"), "exec")
        self.assertIn("NexusMCPServer", source)
        self.assertIn("CurrentNexusMCP", source)
        self.assertNotIn("enable_workspace_tools", source)
        self.assertIn('await nexus.workspace.list(".")', source)
        self.assertIn('await nexus.workspace.read_text("nexus-scenario.txt")', source)
        self.assertIn('await nexus.terminal.run("ls -la -- ."', source)
        self.assertIn('nexus.output.write_text(', source)
        self.assertIn('scope="caller"', source)
        self.assertIn("COPY dist/*.whl", dockerfile)
        self.assertEqual(requirements.strip(), "fastmcp==3.4.7")


class HostedAgentRuntimeReadinessTests(SimpleTestCase):
    def test_docker_runtime_forwards_all_nexus_run_delegate_headers(self):
        self.assertTrue(
            {
                "x-nexus-browser-enabled",
                "x-nexus-browser-delegate-url",
                "x-nexus-browser-delegate-token",
                "x-nexus-mobile-enabled",
                "x-nexus-mobile-delegate-url",
                "x-nexus-mobile-delegate-token",
                "x-nexus-mobile-capabilities",
                "x-nexus-run-messages-url",
                "x-nexus-run-turn",
            }.issubset(runtime_runner._MCP_REQUEST_HEADERS)
        )
        self.assertNotIn("authorization", runtime_runner._MCP_REQUEST_HEADERS)

    def test_windows_connection_abort_is_normalized_for_readiness_retry(self):
        with mock.patch.object(runtime_runner, "urlopen", side_effect=ConnectionAbortedError(10053, "reset")):
            with self.assertRaises(exceptions.APIException):
                runtime_runner._http_request(
                    url="http://127.0.0.1:18080/mcp",
                    method="POST",
                    headers={"Accept": "application/json, text/event-stream"},
                    body=b"{}",
                )

    def test_readiness_requires_successful_initialize_and_closes_session(self):
        responses = [
            runtime_runner.RuntimeMCPResult(status_code=406, headers={}, body=b"not acceptable"),
            runtime_runner.RuntimeMCPResult(
                status_code=200,
                headers={"Mcp-Session-Id": "health-session"},
                body=b'{"jsonrpc":"2.0","id":"health","result":{"protocolVersion":"2025-06-18","capabilities":{},"serverInfo":{"name":"fixture","version":"1"}}}',
            ),
            runtime_runner.RuntimeMCPResult(status_code=204, headers={}, body=b""),
        ]
        with (
            mock.patch.object(runtime_runner, "_http_request", side_effect=responses) as request,
            mock.patch.object(runtime_runner.time, "sleep"),
        ):
            self.assertTrue(runtime_runner._wait_for_mcp(url="http://127.0.0.1:18080/mcp", attempts=2))

        self.assertEqual(request.call_count, 3)
        initialize = request.call_args_list[0].kwargs
        self.assertIn("application/json", initialize["headers"]["Accept"])
        self.assertIn("text/event-stream", initialize["headers"]["Accept"])
        self.assertEqual(initialize["headers"]["MCP-Protocol-Version"], "2025-06-18")
        closed = request.call_args_list[2].kwargs
        self.assertEqual(closed["method"], "DELETE")
        self.assertEqual(closed["headers"]["Mcp-Session-Id"], "health-session")

    def test_successful_http_with_incomplete_initialize_is_not_ready_and_closes_session(self):
        responses = [
            runtime_runner.RuntimeMCPResult(
                status_code=200, headers={"Mcp-Session-Id": "invalid-health-session"},
                body=b'{"jsonrpc":"2.0","id":"health","result":{}}',
            ),
            runtime_runner.RuntimeMCPResult(status_code=204, headers={}, body=b""),
        ]
        with (
            mock.patch.object(runtime_runner, "_http_request", side_effect=responses) as request,
            mock.patch.object(runtime_runner.time, "sleep"),
        ):
            self.assertFalse(runtime_runner._wait_for_mcp(url="http://127.0.0.1:18080/mcp", attempts=1))
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args.kwargs["method"], "DELETE")
        self.assertEqual(request.call_args.kwargs["headers"]["Mcp-Session-Id"], "invalid-health-session")


class PrivateInvocationOutputDownloadTests(TestCase):
    def test_snapshot_download_is_caller_and_display_token_private(self):
        user_model = get_user_model()
        caller_a = user_model.objects.create_user(username="output-caller-a")
        caller_b = user_model.objects.create_user(username="output-caller-b")
        developer = user_model.objects.create_user(username="output-developer")
        consumer = Tenant.objects.create(name="Output Consumer", slug="output-consumer")
        producer = Tenant.objects.create(name="Output Producer", slug="output-producer")
        Membership.objects.create(tenant=consumer, user=caller_a, role=Membership.ROLE_OWNER)
        Membership.objects.create(tenant=consumer, user=caller_b, role=Membership.ROLE_OWNER)
        Membership.objects.create(tenant=producer, user=developer, role=Membership.ROLE_OWNER)
        agent = Agent.objects.create(
            tenant=producer,
            name="Private output Agent",
            visibility=Agent.VISIBILITY_PUBLIC,
            publication_status=Agent.PUBLICATION_PUBLISHED,
            status=Agent.STATUS_ACTIVE,
            created_by=developer,
        )
        identity_request = APIRequestFactory().get("/", HTTP_X_NEXUS_TENANT=str(consumer.id))
        identity_request.user = caller_a
        identity_request.tenant_id = str(consumer.id)
        subject = request_subject(identity_request)
        identity_client = APIClient()
        identity_client.force_authenticate(caller_a)
        token = "caller-a-private-output-token"
        run = AgentDisplayRun.objects.create(
            tenant=producer,
            consumer_tenant=consumer,
            agent=agent,
            run_kind=AgentDisplayRun.KIND_INVOCATION,
            caller_principal_type=subject.principal_type,
            caller_principal_id=subject.principal_id,
            caller_subject_hash=subject.subject_hash,
            display_token_hash=hash_token(token),
            display_token_expires_at=timezone.now() + timedelta(hours=1),
            write_token="private-output-write",
            status=AgentDisplayRun.STATUS_COMPLETED,
        )
        content = b'{"caller":"a"}\n'
        with tempfile.TemporaryDirectory(prefix="nexus-private-output-download-") as storage_root:
            with self.settings(
                NEXUS_DATASET_STORAGE_BACKEND="local",
                NEXUS_DATASET_STORAGE_ROOT=storage_root,
            ):
                stored = get_dataset_storage_backend().save(
                    tenant=producer,
                    dataset_id=str(run.id),
                    file_name="directory-manifest.json",
                    uploaded_file=SimpleUploadedFile("directory-manifest.json", content, content_type="application/json"),
                )
                artifact = AgentOutputArtifact.objects.create(
                    tenant=consumer,
                    agent=agent,
                    run=run,
                    workspace_path="directory-manifest.json",
                    original_file_name="directory-manifest.json",
                    content_type="application/json",
                    size_bytes=stored.size_bytes,
                    sha256=stored.sha256,
                    snapshot_storage_backend=stored.backend,
                    snapshot_object_key=stored.object_key,
                    snapshot_status="ready",
                )
                path = f"/api/v1/agent-runs/{run.id}/outputs/{artifact.id}/download/"
                own = identity_client.get(
                    path,
                    HTTP_X_NEXUS_TENANT=str(consumer.id),
                    HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN=token,
                )
                self.assertEqual(own.status_code, 200)
                self.assertEqual(b"".join(own.streaming_content), content)
                # Downloads are deliberately non-executable attachments; the
                # separate preview endpoint owns content-type-aware rendering.
                self.assertEqual(own["Content-Type"], "application/octet-stream")
                self.assertEqual(own["X-Content-Type-Options"], "nosniff")
                self.assertTrue(own["Content-Disposition"].startswith("attachment;"))

                cross_client = APIClient()
                cross_client.force_authenticate(caller_b)
                cross = cross_client.get(
                    path,
                    HTTP_X_NEXUS_TENANT=str(consumer.id),
                    HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN=token,
                )
                wrong_token = identity_client.get(
                    path,
                    HTTP_X_NEXUS_TENANT=str(consumer.id),
                    HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN="wrong-token",
                )
                self.assertEqual(cross.status_code, 404)
                self.assertEqual(wrong_token.status_code, 404)


@unittest.skipUnless(hosted_agent_e2e_enabled(), "Set NEXUS_HOSTED_AGENT_E2E=1 to run the real Docker/SSH scenario.")
@override_settings(
    NEXUS_WORKSPACE_SSH_RUNNER="paramiko",
    NEXUS_AGENT_RUNTIME_RUNNER="docker",
    NEXUS_AGENT_RUNTIME_CONTAINER_PORT=8000,
    NEXUS_AGENT_RUNTIME_START_ATTEMPTS=100,
    NEXUS_AGENT_RUNTIME_START_DELAY_SECONDS=0.25,
    NEXUS_AGENT_RUNTIME_VERIFY_IMAGE_ON_REGISTER=False,
    ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal"],
)
class HostedAgentSDKDualCallerDockerSSHE2ETests(LiveServerTestCase):
    """Real SDK image + two real SSH Computers + caller-private Runs."""

    host = "0.0.0.0"
    agent_image_ref = ""
    ssh_image_ref = ""
    class_temp: tempfile.TemporaryDirectory[str] | None = None
    private_key = ""
    original_path = ""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if connection.vendor != "postgresql":
            raise AssertionError(
                "NEXUS_HOSTED_AGENT_E2E=1 requires PostgreSQL so concurrent Run writes are genuinely tested."
            )
        cls._require_explicit_prerequisites()
        cls.class_temp = tempfile.TemporaryDirectory(prefix="nexus-hosted-agent-sdk-e2e-")
        root = Path(cls.class_temp.name)
        cls.agent_image_ref = f"nexus-hosted-workspace-inspector:{uuid.uuid4().hex[:12]}"
        cls.ssh_image_ref = f"nexus-hosted-agent-ssh:{uuid.uuid4().hex[:12]}"
        cls._generate_ssh_key(root / "ssh")
        cls.private_key = (root / "ssh").read_text(encoding="utf-8")
        cls._build_ssh_image(root / "ssh-image")
        cls._build_agent_image(root / "agent-image")

    @classmethod
    def tearDownClass(cls):
        for image_ref in (cls.agent_image_ref, cls.ssh_image_ref):
            if image_ref:
                subprocess.run(
                    ["docker", "image", "rm", "-f", image_ref],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
        if cls.class_temp is not None:
            cls.class_temp.cleanup()
        super().tearDownClass()

    @classmethod
    def _require_explicit_prerequisites(cls) -> None:
        docker = shutil.which("docker")
        if docker is None:
            configured = os.environ.get("NEXUS_DOCKER_CLI", "").strip()
            candidates = [
                Path(configured) if configured else None,
                Path("D:/Docker/Desktop/resources/bin/docker.exe"),
                Path("C:/Program Files/Docker/Docker/resources/bin/docker.exe"),
                Path("C:/ProgramData/DockerDesktop/version-bin/docker.exe"),
            ]
            docker_path = next((candidate for candidate in candidates if candidate and candidate.is_file()), None)
            if docker_path is not None:
                cls.original_path = os.environ.get("PATH", "")
                os.environ["PATH"] = str(docker_path.parent) + os.pathsep + cls.original_path
                cls.addClassCleanup(cls._restore_process_path)
                docker = str(docker_path)
        if docker is None:
            raise AssertionError("NEXUS_HOSTED_AGENT_E2E=1 requires the Docker CLI on PATH")
        run_checked([docker, "info"], timeout=30)
        if shutil.which("ssh-keygen") is None:
            raise AssertionError("NEXUS_HOSTED_AGENT_E2E=1 requires ssh-keygen on PATH")

    @classmethod
    def _restore_process_path(cls) -> None:
        if cls.original_path:
            os.environ["PATH"] = cls.original_path

    @classmethod
    def _generate_ssh_key(cls, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        run_checked(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(target)], timeout=30)

    @classmethod
    def _build_ssh_image(cls, context: Path) -> None:
        context.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(cls.class_temp.name) / "ssh.pub", context / "id_ed25519.pub")
        (context / "Dockerfile").write_text(
            """FROM python:3.12-slim
RUN apt-get update \\
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openssh-server \\
    && rm -rf /var/lib/apt/lists/* \\
    && useradd -m -s /bin/sh agent \\
    && mkdir -p /run/sshd /home/agent/.ssh /workspace \\
    && chown -R agent:agent /home/agent /workspace
COPY id_ed25519.pub /home/agent/.ssh/authorized_keys
RUN chown agent:agent /home/agent/.ssh/authorized_keys \\
    && chmod 700 /home/agent/.ssh \\
    && chmod 600 /home/agent/.ssh/authorized_keys \\
    && ssh-keygen -A
EXPOSE 22
CMD ["sh", "-c", "chown -R agent:agent /workspace && exec /usr/sbin/sshd -D -e"]
""",
            encoding="utf-8",
        )
        run_checked(["docker", "build", "-t", cls.ssh_image_ref, "."], cwd=context, timeout=600)

    @classmethod
    def _build_agent_image(cls, context: Path) -> None:
        dist = context / "dist"
        example = context / "examples" / "hosted_workspace_inspector"
        dist.mkdir(parents=True, exist_ok=True)
        example.mkdir(parents=True, exist_ok=True)
        run_checked(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--no-build-isolation",
                "--no-cache-dir",
                "--wheel-dir",
                str(dist),
                str(SDK_ROOT),
            ],
            timeout=300,
        )
        for name in ("agent.py", "requirements.txt"):
            shutil.copy2(EXAMPLE_ROOT / name, example / name)
        dockerfile = EXAMPLE_ROOT / "Dockerfile"
        run_checked(
            ["docker", "build", "-f", str(dockerfile), "-t", cls.agent_image_ref, "."],
            cwd=context,
            timeout=600,
        )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-hosted-agent-callers-")
        self.addCleanup(self.temp.cleanup)
        self.storage_root = Path(self.temp.name) / "object-storage"
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.ssh_containers: list[str] = []
        self.addCleanup(self._cleanup_ssh_containers)

        user_model = get_user_model()
        suffix = uuid.uuid4().hex[:8]
        self.developer = user_model.objects.create_user(
            username=f"hosted-developer-{suffix}", email=f"hosted-developer-{suffix}@example.test", password="password"
        )
        self.caller_a = user_model.objects.create_user(
            username=f"hosted-caller-a-{suffix}", email=f"hosted-caller-a-{suffix}@example.test", password="password"
        )
        self.caller_b = user_model.objects.create_user(
            username=f"hosted-caller-b-{suffix}", email=f"hosted-caller-b-{suffix}@example.test", password="password"
        )
        self.producer = Tenant.objects.create(name="Hosted Agent Producer", slug=f"hosted-producer-{uuid.uuid4().hex[:8]}")
        self.consumer = Tenant.objects.create(name="Hosted Agent Consumer", slug=f"hosted-consumer-{uuid.uuid4().hex[:8]}")
        self.project = Project.objects.create(tenant=self.consumer, name="Shared caller project")
        Membership.objects.create(tenant=self.producer, user=self.developer, role=Membership.ROLE_OWNER)
        Membership.objects.create(tenant=self.consumer, user=self.caller_a, role=Membership.ROLE_OWNER)
        Membership.objects.create(tenant=self.consumer, user=self.caller_b, role=Membership.ROLE_OWNER)

        self.agent = Agent.objects.create(
            tenant=self.producer,
            name=f"Hosted Workspace Inspector {uuid.uuid4().hex[:8]}",
            visibility=Agent.VISIBILITY_PUBLIC,
            publication_status=Agent.PUBLICATION_PUBLISHED,
            status=Agent.STATUS_ACTIVE,
            computer_requirement=Agent.COMPUTER_REQUIRED,
            workspace_capabilities=WORKSPACE_SCOPES,
            current_version="0.26.0-e2e",
            created_by=self.developer,
        )
        version = AgentVersion.objects.create(
            agent=self.agent,
            version="0.26.0-e2e",
            workspace_capabilities=WORKSPACE_SCOPES,
            created_by=self.developer,
        )
        self.image = AgentRuntimeImage.objects.create(
            tenant=self.producer,
            agent=self.agent,
            version=version,
            image_ref=self.agent_image_ref,
            created_by=self.developer,
        )
        self.agent.current_image = self.image
        self.agent.save(update_fields=["current_image", "updated_at"])

        self.developer_client = self._client(self.developer)
        self.client_a = self._client(self.caller_a)
        self.client_b = self._client(self.caller_b)

    def _client(self, user) -> APIClient:
        client = APIClient()
        client.force_authenticate(user)
        return client

    def _consumer_headers(self) -> dict[str, str]:
        return {
            "HTTP_X_NEXUS_TENANT": str(self.consumer.id),
            "HTTP_X_NEXUS_PROJECT": str(self.project.id),
        }

    def _producer_headers(self) -> dict[str, str]:
        return {"HTTP_X_NEXUS_TENANT": str(self.producer.id)}

    def _docker_reachable_live_server_url(self) -> str:
        parsed = urlparse(self.live_server_url)
        host = os.environ.get("NEXUS_DOCKER_HOST_GATEWAY", "host.docker.internal")
        return f"{parsed.scheme}://{host}:{parsed.port}"

    def _start_ssh_computer(self, label: str) -> tuple[Path, int]:
        local_root = Path(self.temp.name) / label
        workspace = local_root / "agents" / str(self.agent.id) / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / f"{label}-only.txt").write_text(f"private file for {label}\n", encoding="utf-8")
        (workspace / "nexus-scenario.txt").write_text(f"controlled probe for {label}\n", encoding="utf-8")
        result = run_checked(
            [
                "docker", "run", "-d", "-p", "127.0.0.1:0:22",
                "-v", f"{local_root.as_posix()}:/workspace", self.ssh_image_ref,
            ],
            timeout=60,
        )
        container_id = result.stdout.strip()
        self.ssh_containers.append(container_id)
        published = run_checked(["docker", "port", container_id, "22/tcp"], timeout=30).stdout.strip().splitlines()[0]
        port = int(published.rsplit(":", 1)[1])
        self._wait_for_port(port)
        return local_root, port

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _wait_for_port(self, port: int) -> None:
        for _ in range(60):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    return
            except OSError:
                time.sleep(0.25)
        self.fail(f"SSH Computer on port {port} did not become reachable")

    def _cleanup_ssh_containers(self) -> None:
        for container_id in self.ssh_containers:
            subprocess.run(["docker", "rm", "-f", container_id], capture_output=True, text=True, timeout=60)

    def _run_live_display_acceptance(self, *, run_a: AgentDisplayRun, run_b: AgentDisplayRun) -> None:
        node = shutil.which("node")
        npx = shutil.which("npx")
        if node is None or npx is None:
            self.fail("The full hosted-Agent scenario requires node and npx for the live private Display acceptance")
        vite = REPO_ROOT / "nexus_web" / "node_modules" / "vite" / "bin" / "vite.js"
        if not vite.exists():
            self.fail("The full hosted-Agent scenario requires installed nexus_web Node dependencies")
        frontend_port = self._free_port()
        backend_target = self.live_server_url.replace("0.0.0.0", "127.0.0.1")
        environment = {
            **os.environ,
            "NEXUS_E2E_API_PROXY_TARGET": backend_target,
        }
        frontend = subprocess.Popen(
            [node, str(vite), "--host", "127.0.0.1", "--port", str(frontend_port)],
            cwd=REPO_ROOT / "nexus_web",
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            self._wait_for_port(frontend_port)
            playwright_environment = {
                **environment,
                "E2E_BASE_URL": f"http://127.0.0.1:{frontend_port}",
                "NEXUS_HOSTED_AGENT_DISPLAY_E2E": "1",
                "NEXUS_HOSTED_AGENT_E2E_TENANT_ID": str(self.consumer.id),
                "NEXUS_HOSTED_AGENT_E2E_PROJECT_ID": str(self.project.id),
                "NEXUS_HOSTED_AGENT_E2E_RUN_A": str(run_a.id),
                "NEXUS_HOSTED_AGENT_E2E_RUN_B": str(run_b.id),
                "NEXUS_HOSTED_AGENT_E2E_CALLER_A_EMAIL": self.caller_a.email,
                "NEXUS_HOSTED_AGENT_E2E_CALLER_A_PASSWORD": "password",
                "NEXUS_HOSTED_AGENT_E2E_CALLER_B_EMAIL": self.caller_b.email,
                "NEXUS_HOSTED_AGENT_E2E_CALLER_B_PASSWORD": "password",
            }
            with self.settings(CSRF_TRUSTED_ORIGINS=[f"http://127.0.0.1:{frontend_port}"]):
                result = subprocess.run(
                    [npx, "playwright", "test", "e2e/hosted-agent-private-display.live.spec.ts", "--project=chromium"],
                    cwd=REPO_ROOT / "nexus_web",
                    env=playwright_environment,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=180,
                )
            self.assertEqual(result.returncode, 0, (result.stdout or "") + "\n" + (result.stderr or ""))
        finally:
            frontend.terminate()
            try:
                frontend.wait(timeout=10)
            except subprocess.TimeoutExpired:
                frontend.kill()
                frontend.wait(timeout=10)

    def _configure_computer(self, client: APIClient, *, label: str, port: int) -> AgentComputerBinding:
        headers = self._consumer_headers()
        created = client.post(
            "/api/v1/workspace-connections/",
            {
                "name": f"{label}-computer",
                "ssh_host": "127.0.0.1",
                "ssh_port": port,
                "ssh_user": "agent",
                "auth_mode": "private_key",
                "private_key": self.private_key,
                "workspace_root": "/workspace",
                "project_id": str(self.project.id),
            },
            format="json",
            **headers,
        )
        self.assertEqual(created.status_code, 201, created.content)
        connection_id = created.json()["data"]["id"]
        tested = None
        for _ in range(10):
            tested = client.post(f"/api/v1/workspace-connections/{connection_id}/test/", **headers)
            if tested.status_code == 200 and tested.json()["data"]["status"] == "succeeded":
                break
            time.sleep(0.5)
        assert tested is not None
        self.assertEqual(tested.status_code, 200, tested.content)
        self.assertEqual(tested.json()["data"]["status"], "succeeded", tested.json()["data"])
        granted = client.put(
            f"/api/v1/agents/{self.agent.id}/workspace-grant/",
            {"scopes": WORKSPACE_SCOPES},
            format="json",
            **headers,
        )
        self.assertEqual(granted.status_code, 200, granted.content)
        bound = client.post(
            f"/api/v1/agents/{self.agent.id}/computer-bindings/",
            {"connection_id": connection_id, "is_default": True},
            format="json",
            **headers,
        )
        self.assertEqual(bound.status_code, 201, bound.content)
        return AgentComputerBinding.objects.get(id=bound.json()["data"]["id"])

    def _mcp(self, client: APIClient, payload: dict, *, session_id: str = "", **extra_headers):
        headers = {
            **self._consumer_headers(),
            "HTTP_ACCEPT": "application/json, text/event-stream",
            "HTTP_MCP_PROTOCOL_VERSION": "2025-06-18",
            **extra_headers,
        }
        if session_id:
            headers["HTTP_MCP_SESSION_ID"] = session_id
        return client.post(
            f"/api/v1/agents/{self.agent.id}/mcp/",
            payload,
            format="json",
            **headers,
        )

    def _delete_mcp_session(self, client: APIClient, session_id: str):
        return client.delete(
            f"/api/v1/agents/{self.agent.id}/mcp/",
            **self._consumer_headers(),
            HTTP_ACCEPT="application/json, text/event-stream",
            HTTP_MCP_PROTOCOL_VERSION="2025-06-18",
            HTTP_MCP_SESSION_ID=session_id,
        )

    @staticmethod
    def _response_bytes(response) -> bytes:
        cached = getattr(response, "_nexus_e2e_body", None)
        if cached is not None:
            return cached
        if getattr(response, "streaming", False):
            body = b"".join(response.streaming_content)
        else:
            body = response.content
        response._nexus_e2e_body = body
        return body

    def _json_rpc_response(self, response) -> dict:
        body = self._response_bytes(response).strip()
        if not body:
            return {}
        lines = body.splitlines()
        if not any(line.startswith(b"data:") for line in lines):
            return json.loads(body)
        messages = []
        for line in lines:
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if not data or data == b"[DONE]":
                continue
            value = json.loads(data)
            if isinstance(value, dict):
                messages.append(value)
        return next(
            (message for message in reversed(messages) if "result" in message or "error" in message),
            messages[-1] if messages else {},
        )

    def _initialize_session(self, client: APIClient, label: str) -> str:
        response = None
        for attempt in range(3):
            response = self._mcp(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": f"initialize-{label}-{attempt + 1}",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": f"nexus-e2e-{label}", "version": "1.0"},
                    },
                },
            )
            if response.status_code == 200:
                break
            body = self._response_bytes(response)
            retryable_transport_error = any(
                marker in body
                for marker in (b"timed out", b"WinError 10053", b"WinError 10054")
            )
            if response.status_code != 500 or not retryable_transport_error:
                break
            time.sleep(0.5)
        assert response is not None
        if response.status_code != 200:
            deployment = AgentRuntimeDeployment.objects.filter(agent=self.agent, env="prod").first()
            logs = ""
            if deployment is not None and deployment.container_id:
                logged = subprocess.run(
                    ["docker", "logs", "--tail", "200", deployment.container_id],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                )
                logs = (logged.stdout or "") + (logged.stderr or "")
            self.fail(f"MCP initialize failed: {self._response_bytes(response)!r}\nRuntime logs:\n{logs}")
        self._json_rpc_response(response)
        session_id = response.headers.get("Mcp-Session-Id", "")
        self.assertTrue(session_id)
        initialized = self._mcp(
            client,
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            session_id=session_id,
        )
        self.assertLess(initialized.status_code, 400, self._response_bytes(initialized))
        self._response_bytes(initialized)
        return session_id

    @staticmethod
    def _tool_payload(label: str) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": f"inspect-{label}",
            "method": "tools/call",
            "params": {"name": "inspect_workspace", "arguments": {}},
        }

    def _tool_result(self, response) -> dict:
        if response.status_code != 200:
            deployment = AgentRuntimeDeployment.objects.filter(agent=self.agent, env="prod").first()
            logs = ""
            if deployment is not None and deployment.container_id:
                logged = subprocess.run(
                    ["docker", "logs", "--tail", "300", deployment.container_id],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                )
                logs = (logged.stdout or "") + (logged.stderr or "")
            run_states = list(
                AgentDisplayRun.objects.filter(agent=self.agent, run_kind=AgentDisplayRun.KIND_INVOCATION)
                .order_by("created_at")
                .values("id", "status", "error_code")
            )
            self.fail(
                f"Hosted Workspace Inspector request failed: {self._response_bytes(response)!r}"
                f"\nRun states: {run_states!r}\nRuntime logs:\n{logs}"
            )
        payload = self._json_rpc_response(response)
        result = payload["result"]
        structured = result.get("structuredContent") or result.get("structured_content")
        if isinstance(structured, dict):
            return structured.get("result", structured)
        text = next(item["text"] for item in result.get("content", []) if item.get("type") == "text")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            self.fail(f"Hosted Workspace Inspector returned a non-JSON Tool result: {payload!r}")

    def test_two_callers_get_private_computers_runs_memory_and_outputs(self):
        docker_base_url = self._docker_reachable_live_server_url()
        with self.settings(
            NEXUS_AGENT_DISPLAY_EVENTS_BASE_URL=docker_base_url,
            NEXUS_AGENT_WORKSPACE_API_BASE_URL=docker_base_url,
            NEXUS_DATASET_STORAGE_BACKEND="local",
            NEXUS_DATASET_STORAGE_ROOT=str(self.storage_root),
            NEXUS_LOCAL_TENANT_SLUG=self.consumer.slug,
            NEXUS_LOCAL_REMOTE_WORKSPACE_LIMIT=2,
        ):
            deployed = self.developer_client.post(
                f"/api/v1/agents/{self.agent.id}/runtime/deployments/",
                {"image_id": str(self.image.id), "env": "prod"},
                format="json",
                **self._producer_headers(),
            )
            if deployed.status_code != 201:
                error = AgentRuntimeDeployment.objects.filter(agent=self.agent, env="prod").values_list("last_error", flat=True).first()
                self.fail(f"Runtime deployment failed: {deployed.content!r}; runtime_error={error}")

            try:
                before = AgentDisplayRun.objects.filter(run_kind=AgentDisplayRun.KIND_INVOCATION).count()
                missing = self._mcp(self.client_a, self._tool_payload("missing-computer"))
                self.assertEqual(missing.status_code, 409, missing.content)
                self.assertIn("caller-owned Computer", missing.content.decode("utf-8"))
                self.assertEqual(AgentDisplayRun.objects.filter(run_kind=AgentDisplayRun.KIND_INVOCATION).count(), before)

                local_a, port_a = self._start_ssh_computer("caller-a")
                local_b, port_b = self._start_ssh_computer("caller-b")
                binding_a = self._configure_computer(self.client_a, label="caller-a", port=port_a)
                binding_b = self._configure_computer(self.client_b, label="caller-b", port=port_b)
                self.assertNotEqual(binding_a.id, binding_b.id)

                session_a = self._initialize_session(self.client_a, "a")
                session_b = self._initialize_session(self.client_b, "b")
                self.assertNotEqual(session_a, session_b)
                listed = self._mcp(
                    self.client_a,
                    {"jsonrpc": "2.0", "id": "list-a", "method": "tools/list", "params": {}},
                    session_id=session_a,
                )
                self.assertEqual(listed.status_code, 200, self._response_bytes(listed))
                self._json_rpc_response(listed)
                self.assertEqual(AgentDisplayRun.objects.filter(run_kind=AgentDisplayRun.KIND_INVOCATION).count(), before)
                crossed_session = self._mcp(
                    self.client_b,
                    {"jsonrpc": "2.0", "id": "cross-session", "method": "tools/list", "params": {}},
                    session_id=session_a,
                )
                self.assertEqual(crossed_session.status_code, 404, self._response_bytes(crossed_session))

                barrier = threading.Barrier(2)

                def invoke(client: APIClient, session_id: str, label: str):
                    close_old_connections()
                    try:
                        barrier.wait(timeout=10)
                        return self._mcp(
                            client,
                            self._tool_payload(label),
                            session_id=session_id,
                            HTTP_X_NEXUS_AGUI_RUN_ID="forged-run",
                            HTTP_X_NEXUS_AGUI_TOKEN="forged-token",
                            HTTP_X_NEXUS_WORKSPACE_DELEGATE_TOKEN="forged-delegate",
                        )
                    finally:
                        close_old_connections()

                with ThreadPoolExecutor(max_workers=2) as executor:
                    future_a = executor.submit(invoke, self.client_a, session_a, "caller-a")
                    future_b = executor.submit(invoke, self.client_b, session_b, "caller-b")
                    response_a = future_a.result(timeout=120)
                    response_b = future_b.result(timeout=120)

                result_a = self._tool_result(response_a)
                result_b = self._tool_result(response_b)
                names_a = {entry["name"] for entry in result_a["entries"]}
                names_b = {entry["name"] for entry in result_b["entries"]}
                self.assertIn("caller-a-only.txt", names_a)
                self.assertNotIn("caller-b-only.txt", names_a)
                self.assertIn("caller-b-only.txt", names_b)
                self.assertNotIn("caller-a-only.txt", names_b)
                self.assertNotIn("controlled probe for", json.dumps(result_a))
                self.assertNotIn("controlled probe for", json.dumps(result_b))

                run_id_a = response_a.headers["X-Nexus-Agent-Run-Id"]
                run_id_b = response_b.headers["X-Nexus-Agent-Run-Id"]
                token_a = response_a.headers["X-Nexus-Agent-Display-Token"]
                token_b = response_b.headers["X-Nexus-Agent-Display-Token"]
                self.assertNotEqual(run_id_a, run_id_b)
                self.assertNotEqual(token_a, token_b)
                self.assertNotEqual(run_id_a, "forged-run")

                run_a = AgentDisplayRun.objects.get(id=run_id_a)
                run_b = AgentDisplayRun.objects.get(id=run_id_b)
                self.assertEqual(run_a.status, AgentDisplayRun.STATUS_COMPLETED)
                self.assertEqual(run_b.status, AgentDisplayRun.STATUS_COMPLETED)
                self.assertEqual(run_a.computer_binding_id, binding_a.id)
                self.assertEqual(run_b.computer_binding_id, binding_b.id)
                self.assertNotEqual(run_a.caller_subject_hash, run_b.caller_subject_hash)
                self.assertNotEqual(run_a.output_root, run_b.output_root)
                self.assertEqual(run_a.workspace_capabilities_snapshot, WORKSPACE_SCOPES)
                self.assertEqual(run_b.workspace_capabilities_snapshot, WORKSPACE_SCOPES)
                self.assertEqual(AgentRuntimeInvocation.objects.filter(display_run__in=[run_a, run_b]).count(), 2)

                for run, own_name, other_name in (
                    (run_a, "caller-a-only.txt", "caller-b-only.txt"),
                    (run_b, "caller-b-only.txt", "caller-a-only.txt"),
                ):
                    seq = list(run.events.order_by("seq").values_list("seq", flat=True))
                    self.assertEqual(seq, list(range(1, len(seq) + 1)))
                    event_types = set(run.events.values_list("event_type", flat=True))
                    self.assertTrue({"RUN_STARTED", "STEP_STARTED", "STEP_FINISHED", "TOOL_CALL_START", "TOOL_CALL_END", "RUN_FINISHED"}.issubset(event_types))
                    event_json = json.dumps(list(run.events.values_list("payload_json", flat=True)))
                    self.assertNotIn("forged-token", event_json)
                    self.assertNotIn("forged-delegate", event_json)
                    terminal = WorkspaceTerminalSession.objects.get(display_run=run)
                    transcript = "\n".join(terminal.transcript.values_list("data", flat=True))
                    self.assertIn(own_name, transcript)
                    self.assertNotIn(other_name, transcript)
                    self.assertEqual(terminal.viewer_mode, "read_only")

                memories_a = AgentMemoryItem.objects.filter(source_run=run_a)
                memories_b = AgentMemoryItem.objects.filter(source_run=run_b)
                self.assertEqual(memories_a.count(), 1)
                self.assertEqual(memories_b.count(), 1)
                memory_a = memories_a.get()
                memory_b = memories_b.get()
                self.assertEqual(memory_a.scope, AgentMemoryItem.SCOPE_CALLER)
                self.assertEqual(memory_b.scope, AgentMemoryItem.SCOPE_CALLER)
                self.assertEqual(memory_a.consent_status, AgentMemoryItem.CONSENT_APPROVED)
                self.assertNotEqual(memory_a.caller_subject_hash, memory_b.caller_subject_hash)
                self.assertEqual(memory_a.source_event_ids, [str(memory_a.source_run.events.get(payload_json__name="nexus.memory.item").id)])
                recalled_a = recall_invocation_memory(run_id=str(run_a.id), token=run_a.write_token)
                recalled_b = recall_invocation_memory(run_id=str(run_b.id), token=run_b.write_token)
                recalled_a_ids = {item["id"] for item in recalled_a["items"]}
                recalled_b_ids = {item["id"] for item in recalled_b["items"]}
                self.assertIn(str(memory_a.id), recalled_a_ids)
                self.assertNotIn(str(memory_b.id), recalled_a_ids)
                self.assertIn(str(memory_b.id), recalled_b_ids)
                self.assertNotIn(str(memory_a.id), recalled_b_ids)

                artifact_a = AgentOutputArtifact.objects.get(run=run_a, workspace_path="directory-manifest.json")
                artifact_b = AgentOutputArtifact.objects.get(run=run_b, workspace_path="directory-manifest.json")
                self.assertEqual(artifact_a.snapshot_status, "ready", artifact_a.snapshot_error)
                self.assertEqual(artifact_b.snapshot_status, "ready", artifact_b.snapshot_error)
                self.assertNotEqual(artifact_a.snapshot_object_key, artifact_b.snapshot_object_key)
                backend_a = get_dataset_storage_backend(artifact_a.snapshot_storage_backend)
                backend_b = get_dataset_storage_backend(artifact_b.snapshot_storage_backend)
                with backend_a.open(object_key=artifact_a.snapshot_object_key) as stream:
                    output_a = stream.read()
                with backend_b.open(object_key=artifact_b.snapshot_object_key) as stream:
                    output_b = stream.read()
                self.assertEqual(artifact_a.sha256, hashlib.sha256(output_a).hexdigest())
                self.assertEqual(artifact_b.sha256, hashlib.sha256(output_b).hexdigest())
                self.assertIn(b"caller-a-only.txt", output_a)
                self.assertNotIn(b"caller-b-only.txt", output_a)
                self.assertIn(b"caller-b-only.txt", output_b)
                self.assertNotIn(b"caller-a-only.txt", output_b)
                self.assertNotIn(b"controlled probe for", output_a + output_b)
                download_a = self.client_a.get(
                    f"/api/v1/agent-runs/{run_a.id}/outputs/{artifact_a.id}/download/",
                    HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN=token_a,
                    **self._consumer_headers(),
                )
                download_b = self.client_b.get(
                    f"/api/v1/agent-runs/{run_b.id}/outputs/{artifact_b.id}/download/",
                    HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN=token_b,
                    **self._consumer_headers(),
                )
                self.assertEqual(download_a.status_code, 200)
                self.assertEqual(download_b.status_code, 200)
                self.assertEqual(b"".join(download_a.streaming_content), output_a)
                self.assertEqual(b"".join(download_b.streaming_content), output_b)

                own_display = self.client_a.get(
                    f"/api/v1/agent-runs/{run_a.id}/display/",
                    HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN=token_a,
                    **self._consumer_headers(),
                )
                cross_display = self.client_b.get(
                    f"/api/v1/agent-runs/{run_a.id}/display/",
                    HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN=token_a,
                    **self._consumer_headers(),
                )
                wrong_token = self.client_a.get(
                    f"/api/v1/agent-runs/{run_a.id}/display/",
                    HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN=token_b,
                    **self._consumer_headers(),
                )
                self.assertEqual(own_display.status_code, 200, own_display.content)
                self.assertEqual(own_display.json()["data"]["computer"]["viewer_mode"], "read_only")
                self.assertEqual(cross_display.status_code, 404)
                self.assertEqual(wrong_token.status_code, 404)

                developer_runs = self.developer_client.get(
                    f"/api/v1/agents/{self.agent.id}/display-runs/",
                    **self._producer_headers(),
                )
                self.assertEqual(developer_runs.status_code, 200, developer_runs.content)
                serialized = json.dumps(developer_runs.json())
                self.assertIn(str(run_a.id), serialized)
                self.assertIn(str(run_b.id), serialized)
                self.assertNotIn("127.0.0.1", serialized)
                self.assertNotIn("ssh_user", serialized)
                self.assertNotIn("caller-a-only.txt", serialized)

                expected_probe_a = hashlib.sha256(
                    (local_a / "agents" / str(self.agent.id) / "workspace" / "nexus-scenario.txt").read_bytes()
                ).hexdigest()
                expected_probe_b = hashlib.sha256(
                    (local_b / "agents" / str(self.agent.id) / "workspace" / "nexus-scenario.txt").read_bytes()
                ).hexdigest()
                self.assertEqual(result_a["probe"]["sha256"], expected_probe_a)
                self.assertEqual(result_b["probe"]["sha256"], expected_probe_b)
                self.assertTrue((local_a / "agents" / str(self.agent.id) / "runs" / str(run_a.id) / "outputs" / "directory-manifest.json").exists())
                self.assertTrue((local_b / "agents" / str(self.agent.id) / "runs" / str(run_b.id) / "outputs" / "directory-manifest.json").exists())
                self._run_live_display_acceptance(run_a=run_a, run_b=run_b)
                deleted_a = self._delete_mcp_session(self.client_a, session_a)
                deleted_b = self._delete_mcp_session(self.client_b, session_b)
                self.assertLess(deleted_a.status_code, 400, self._response_bytes(deleted_a))
                self.assertLess(deleted_b.status_code, 400, self._response_bytes(deleted_b))
                closed = self._mcp(
                    self.client_a,
                    {"jsonrpc": "2.0", "id": "closed-session", "method": "tools/list", "params": {}},
                    session_id=session_a,
                )
                self.assertEqual(closed.status_code, 404, self._response_bytes(closed))
            finally:
                self.developer_client.post(
                    f"/api/v1/agents/{self.agent.id}/runtime/deployments/stop/",
                    {"env": "prod"},
                    format="json",
                    **self._producer_headers(),
                )
