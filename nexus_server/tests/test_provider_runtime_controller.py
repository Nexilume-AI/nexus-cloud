from __future__ import annotations

import socket
import tempfile
import threading
import time
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from apps.providers.provider_controller import (
    ControllerProviderRuntimeRunner,
    ProviderRuntimeControllerClient,
    ProviderRuntimeControllerError,
    ProviderRuntimeControllerServer,
    dispatch_provider_runtime_command,
    ensure_provider_runtime_controller_dependencies,
)
from apps.common.crypto import encrypt_secret
from apps.providers.models import ProviderRuntimeAccount
from apps.tenancy.models import Tenant
from apps.providers.runtime_runner import (
    CodexProxyRuntimeAdapter,
    _docker_stop,
    docker_host_storage_path,
    docker_run_command,
)


class ProviderRuntimeControllerProtocolTests(SimpleTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        if hasattr(socket, "AF_UNIX"):
            self.socket_path = str(Path(self.tempdir.name) / "provider.sock")
        else:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", 0))
                self.socket_path = f"tcp://127.0.0.1:{listener.getsockname()[1]}"
        self.server = None
        self.thread = None

    def tearDown(self) -> None:
        if self.server is not None:
            self.server.stop()
        if self.thread is not None:
            self.thread.join(timeout=3)

    def _start(self, dispatcher):
        self.server = ProviderRuntimeControllerServer(
            socket_path=self.socket_path,
            token="controller-secret",
            dispatcher=dispatcher,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                ProviderRuntimeControllerClient(
                    socket_path=self.socket_path, token="controller-secret"
                ).ping()
                break
            except ProviderRuntimeControllerError:
                time.sleep(0.01)
        else:
            self.fail("Provider Runtime Controller did not become ready")

    def test_authenticated_ping_and_runner_results_use_the_socket_protocol(self) -> None:
        calls = []

        def dispatch(request):
            calls.append(request)
            if request["action"] == "ping":
                return {"status": "ready", "protocol_version": 1}
            return {
                "container_id": "container-1",
                "internal_login_url": "http://runtime:8080/auth/login",
                "internal_api_url": "http://runtime:8080/v1",
            }

        self._start(dispatch)
        client = ProviderRuntimeControllerClient(socket_path=self.socket_path, token="controller-secret")
        self.assertEqual(client.ping()["status"], "ready")
        runner = ControllerProviderRuntimeRunner(client=client)
        result = runner.start(runtime=SimpleNamespace(id="runtime-1"), proxy_api_key="must-not-cross-socket")

        self.assertEqual(result.container_id, "container-1")
        self.assertEqual(calls[-1]["runtime_id"], "runtime-1")
        self.assertEqual(calls[-1]["payload"], {})
        self.assertNotIn("must-not-cross-socket", str(calls))

    def test_wrong_controller_token_is_rejected(self) -> None:
        self._start(lambda request: {"status": "ready"})
        client = ProviderRuntimeControllerClient(socket_path=self.socket_path, token="wrong-secret")

        with self.assertRaisesRegex(ProviderRuntimeControllerError, "authentication failed"):
            client.ping()

    def test_restore_returns_health_for_recovered_endpoint_without_sending_secret(self) -> None:
        calls = []

        def dispatch(request):
            calls.append(request)
            if request["action"] == "ping":
                return {"status": "ready", "protocol_version": 1}
            return {
                "container_id": "restored-container",
                "internal_login_url": "http://runtime:8080/auth/login",
                "internal_api_url": "http://runtime:8080/v1",
                "health": {"healthy": True, "login_required": False, "reason": "ready", "recovery_recommended": False},
            }

        self._start(dispatch)
        runner = ControllerProviderRuntimeRunner(client=ProviderRuntimeControllerClient(socket_path=self.socket_path, token="controller-secret"))
        result = runner.restore(runtime=SimpleNamespace(id="runtime-1"), proxy_api_key="must-not-cross-socket")
        self.assertTrue(result.health.healthy)
        self.assertEqual(result.container_id, "restored-container")
        self.assertEqual(calls[-1]["action"], "restore")
        self.assertNotIn("must-not-cross-socket", str(calls))


class ProviderRuntimeContainerNetworkTests(SimpleTestCase):
    @override_settings(NEXUS_PROVIDER_RUNTIME_NETWORK_ENDPOINTS=False)
    def test_host_mode_asks_docker_to_allocate_the_port_atomically(self) -> None:
        runtime = SimpleNamespace(id="runtime-id", tenant_id="tenant-id", storage_path="tenant/runtime")
        adapter = CodexProxyRuntimeAdapter()
        with override_settings(NEXUS_PROVIDER_RUNTIME_STORAGE_ROOT=tempfile.gettempdir()):
            command = docker_run_command(
                runtime=runtime,
                adapter=adapter,
                image="nexus-codex-proxy:runtime",
                proxy_api_key="secret",
                host_port=0,
            )

        self.assertIn("127.0.0.1::8080", command)

    @patch("apps.providers.runtime_runner.subprocess.run")
    def test_docker_stop_rejects_a_failed_removal(self, run) -> None:
        run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="daemon unavailable")

        with self.assertRaisesRegex(Exception, "daemon unavailable"):
            _docker_stop("container-id")

    @patch("apps.providers.runtime_runner.subprocess.run")
    def test_docker_stop_is_idempotent_when_container_is_already_gone(self, run) -> None:
        run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="No such container: container-id")

        _docker_stop("container-id")

    @override_settings(
        NEXUS_PROVIDER_RUNTIME_NETWORK_ENDPOINTS=True,
        NEXUS_PROVIDER_RUNTIME_DOCKER_NETWORK="nexus-provider-runtime",
    )
    def test_container_network_mode_does_not_publish_a_host_port(self) -> None:
        runtime = SimpleNamespace(id="runtime-id", tenant_id="tenant-id", storage_path="tenant/runtime")
        adapter = CodexProxyRuntimeAdapter()
        with override_settings(NEXUS_PROVIDER_RUNTIME_STORAGE_ROOT=tempfile.gettempdir()):
            command = docker_run_command(
                runtime=runtime,
                adapter=adapter,
                image="nexus-codex-proxy:runtime",
                proxy_api_key="secret",
                host_port=19080,
            )

        self.assertIn("nexus-provider-runtime", command)
        self.assertNotIn("-p", command)
        self.assertNotIn("127.0.0.1:19080:8080", command)

    def test_controller_storage_path_is_translated_to_the_host_bind_root(self) -> None:
        with tempfile.TemporaryDirectory() as controller_root, tempfile.TemporaryDirectory() as host_root:
            nested = Path(controller_root) / "tenant" / "runtime" / "data"
            nested.mkdir(parents=True)
            with override_settings(
                NEXUS_PROVIDER_RUNTIME_STORAGE_ROOT=controller_root,
                NEXUS_PROVIDER_RUNTIME_HOST_STORAGE_ROOT=host_root,
            ):
                translated = docker_host_storage_path(nested)

        self.assertEqual(translated, Path(host_root).resolve() / "tenant" / "runtime" / "data")

    @patch("apps.providers.provider_controller.subprocess.run")
    @patch("apps.providers.provider_controller.shutil.which", return_value="/usr/bin/docker")
    def test_controller_dependency_check_requires_a_live_docker_engine(self, _which, run) -> None:
        run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="denied")
        with self.assertRaisesRegex(ProviderRuntimeControllerError, "Docker Engine is unavailable"):
            ensure_provider_runtime_controller_dependencies()


class ProviderRuntimeControllerDispatchTests(TestCase):
    @patch("apps.providers.provider_controller.DockerProviderRuntimeRunner")
    def test_restore_probes_new_endpoint_before_caller_commits_it(self, docker_runner):
        owner = get_user_model().objects.create_user(username="restore-controller-owner")
        tenant = Tenant.objects.create(name="Restore Controller", slug="restore-controller")
        runtime = ProviderRuntimeAccount.objects.create(
            tenant=tenant, owner=owner, name="Restore test", runtime_type="codex_proxy",
            encrypted_proxy_api_key=encrypt_secret("stable-secret"), internal_api_url="http://old:8080/v1",
        )
        docker_runner.return_value.restore.return_value = SimpleNamespace(
            container_id="restored-container", internal_login_url="http://new:8080/auth/login", internal_api_url="http://new:8080/v1",
        )

        def health_check(*, runtime):
            self.assertEqual(runtime.internal_api_url, "http://new:8080/v1")
            self.assertEqual(ProviderRuntimeAccount.objects.get(pk=runtime.pk).internal_api_url, "http://old:8080/v1")
            return SimpleNamespace(healthy=True, login_required=False, reason="ready", recovery_recommended=False)

        docker_runner.return_value.health_check.side_effect = health_check
        result = dispatch_provider_runtime_command({"action": "restore", "runtime_id": str(runtime.id), "payload": {}})
        self.assertTrue(result["health"]["healthy"])
        self.assertNotIn("stable-secret", str(result))

    @patch("apps.providers.provider_controller.DockerProviderRuntimeRunner")
    def test_start_loads_the_encrypted_proxy_key_inside_the_controller(self, docker_runner) -> None:
        owner = get_user_model().objects.create_user(username="controller-owner")
        tenant = Tenant.objects.create(name="Controller Tenant", slug="controller-tenant")
        runtime = ProviderRuntimeAccount.objects.create(
            tenant=tenant,
            owner=owner,
            name="Controller Runtime",
            runtime_type=ProviderRuntimeAccount.RUNTIME_CODEX_PROXY,
            encrypted_proxy_api_key=encrypt_secret("database-owned-secret"),
        )
        docker_runner.return_value.start.return_value = SimpleNamespace(
            container_id="container-1",
            internal_login_url="http://runtime:8080/auth/login",
            internal_api_url="http://runtime:8080/v1",
        )

        result = dispatch_provider_runtime_command(
            {"action": "start", "runtime_id": str(runtime.id), "payload": {}}
        )

        self.assertEqual(result["container_id"], "container-1")
        docker_runner.return_value.start.assert_called_once_with(
            runtime=runtime,
            proxy_api_key="database-owned-secret",
        )
