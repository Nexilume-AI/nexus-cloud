"""Original Provider recovery guards shared by both distributions.

The explicit subprocess/HTTP mocks are preserved unit boundaries, not live
Docker, network or recovery acceptance claims.
"""
from types import SimpleNamespace
from unittest.mock import Mock, patch
from apps.providers.runtime_runner import (
    DockerProviderRuntimeRunner, ProviderRuntimeHealthResult,
    ProviderRuntimeStartResult, probe_openai_compatible_health,
)


class ProviderProcessRecoveryGuards:
    @patch("apps.providers.runtime_runner.http_status")
    def test_slow_proxy_recovers_on_confirmation_without_restart(self, probe):
        probe.side_effect = [(None, "timed out"), (200, "OK")]
        runtime = SimpleNamespace(internal_api_url="http://runtime.test/v1", encrypted_proxy_api_key="")
        health = probe_openai_compatible_health(runtime=runtime)
        self.assertTrue(health.healthy)
        self.assertFalse(health.recovery_recommended)
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(probe.call_args.kwargs["timeout"], 15)

    @patch("apps.providers.runtime_runner.http_status")
    def test_persistent_transport_failure_still_recommends_recovery(self, probe):
        probe.return_value = (None, "private transport information")
        health = probe_openai_compatible_health(runtime=SimpleNamespace(
            internal_api_url="http://runtime.test/v1", encrypted_proxy_api_key=""
        ))
        self.assertFalse(health.healthy)
        self.assertTrue(health.recovery_recommended)
        self.assertEqual(probe.call_count, 2)
        self.assertNotIn("private", health.reason)

    @patch("apps.providers.runtime_runner.http_status")
    def test_healthy_or_http_error_does_not_retry(self, probe):
        for status in (200, 401, 403, 429, 500, 503):
            with self.subTest(status=status):
                probe.reset_mock()
                probe.return_value = (status, "HTTP response")
                health = probe_openai_compatible_health(runtime=SimpleNamespace(
                    internal_api_url="http://runtime.test/v1", encrypted_proxy_api_key=""
                ))
                probe.assert_called_once()
                self.assertFalse(health.recovery_recommended)

    @patch("apps.providers.runtime_runner.urlopen")
    def test_health_probe_closes_without_reading_streamed_catalog(self, urlopen):
        from apps.providers.runtime_runner import http_status
        response = urlopen.return_value.__enter__.return_value
        response.status, response.reason = 200, "OK"
        self.assertEqual(http_status(url="http://runtime.test/v1/models", timeout=15), (200, "OK"))
        response.read.assert_not_called()
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 15)
        urlopen.return_value.__exit__.assert_called_once()

    @patch("apps.providers.runtime_runner.wait_for_runtime_port")
    @patch("apps.providers.runtime_runner._docker_host_port", return_value=29080)
    @patch("apps.providers.runtime_runner._run_docker")
    @patch("apps.providers.runtime_runner._container_is_running", return_value=True)
    @patch("apps.providers.runtime_runner._container_has_managed_restart_policy", return_value=True)
    @patch("apps.providers.runtime_runner._provider_runtime_container_id", return_value="current-container")
    def test_stale_endpoint_is_refreshed_before_deciding_to_restart(self, _id, _policy, _running, run, _port, _wait):
        runtime = SimpleNamespace(runtime_type="codex_proxy", container_id="old-container", internal_api_url="http://127.0.0.1:19080/v1")
        runner = DockerProviderRuntimeRunner()
        def health(*, runtime):
            self.assertEqual(runtime.container_id, "current-container")
            self.assertEqual(runtime.internal_api_url, "http://127.0.0.1:29080/v1")
            return ProviderRuntimeHealthResult(True, False, "ready")
        with patch.object(runner, "health_check", side_effect=health):
            result = runner.restore(runtime=runtime, proxy_api_key="stable-key")
        run.assert_not_called()
        self.assertEqual(result.internal_api_url, "http://127.0.0.1:29080/v1")
        self.assertEqual(runtime.internal_api_url, "http://127.0.0.1:19080/v1")

    @patch("apps.providers.runtime_runner.wait_for_runtime_port")
    @patch("apps.providers.runtime_runner._docker_host_port", return_value=29080)
    @patch("apps.providers.runtime_runner._run_docker")
    @patch("apps.providers.runtime_runner._container_is_running", return_value=False)
    @patch("apps.providers.runtime_runner._container_has_managed_restart_policy", return_value=True)
    @patch("apps.providers.runtime_runner._provider_runtime_container_id", return_value="stopped-container")
    def test_stopped_container_starts_before_reading_published_port(self, _id, _policy, _running, run, port, _wait):
        def current_port(**kwargs):
            run.assert_called_once_with(["docker", "start", "stopped-container"], timeout=30)
            return 29080
        port.side_effect = current_port
        runner = DockerProviderRuntimeRunner()
        with patch.object(runner, "start") as recreate:
            restored = runner.restore(runtime=SimpleNamespace(runtime_type="codex_proxy"), proxy_api_key="stable-key")
        recreate.assert_not_called()
        self.assertEqual(restored.container_id, "stopped-container")

    def test_missing_published_port_recreates_container_with_same_credential(self):
        from rest_framework.exceptions import APIException

        runtime = SimpleNamespace(runtime_type="codex_proxy")
        runner = DockerProviderRuntimeRunner()
        expected = ProviderRuntimeStartResult("replacement", "http://localhost/login", "http://localhost/v1")
        with patch("apps.providers.runtime_runner._provider_runtime_container_id", return_value="broken-port-container"), \
             patch("apps.providers.runtime_runner._container_has_managed_restart_policy", return_value=True), \
             patch("apps.providers.runtime_runner._container_is_running", return_value=True), \
             patch("apps.providers.runtime_runner._docker_host_port", side_effect=APIException("No public port")), \
             patch.object(runner, "health_check", return_value=ProviderRuntimeHealthResult(True, False, "ready")), \
             patch.object(runner, "start", return_value=expected) as recreate:
            restored = runner.restore(runtime=runtime, proxy_api_key="preserved-key")
        self.assertEqual(restored, expected)
        recreate.assert_called_once_with(runtime=runtime, proxy_api_key="preserved-key")

    @patch("apps.providers.management.commands.run_provider_maintenance.time.sleep", side_effect=[None, InterruptedError])
    @patch("apps.providers.management.commands.run_provider_maintenance.reconcile_provider_runtime_health_task", return_value={})
    def test_slow_catalog_does_not_block_health_or_start_overlapping_catalogs(self, health, _sleep):
        from io import StringIO
        from apps.providers.management.commands.run_provider_maintenance import Command

        command = Command(stdout=StringIO(), stderr=StringIO())
        executor = Mock()
        executor.submit.return_value.done.return_value = False
        with self.assertRaises(InterruptedError):
            command._run(options={"once": False}, executor=executor, health_interval=5)
        self.assertEqual(health.call_count, 2)
        executor.submit.assert_called_once()

    def test_maintenance_once_reports_catalog_failure_without_exception_details(self):
        from io import StringIO
        from apps.providers.management.commands.run_provider_maintenance import Command

        output = StringIO()
        command = Command(stderr=output)
        future = Mock()
        future.result.side_effect = RuntimeError("secret controller URL")
        command._report_catalog(future)
        self.assertIn("retrying", output.getvalue())
        self.assertNotIn("secret", output.getvalue())

    @patch("apps.providers.runtime_runner.http_status")
    def test_transport_failure_recommends_recovery_but_http_errors_do_not(self, http_status):
        runtime = SimpleNamespace(internal_api_url="http://runtime.test/v1", encrypted_proxy_api_key="")
        for status in (401, 429, 500, 503, 504):
            with self.subTest(status=status):
                http_status.return_value = (status, "timeout from upstream")
                health = probe_openai_compatible_health(runtime=runtime)
                self.assertFalse(health.recovery_recommended)
                self.assertFalse(health.healthy)
        http_status.return_value = (None, "secret internal transport details")
        health = probe_openai_compatible_health(runtime=runtime)
        self.assertTrue(health.recovery_recommended)
        self.assertNotIn("secret", health.reason)

    @patch("apps.providers.runtime_runner.subprocess.run")
    def test_stopped_container_and_inspect_failure_are_recoverable(self, run):
        runtime = SimpleNamespace(container_id="managed-container")
        run.return_value = SimpleNamespace(returncode=0, stdout="false", stderr="")
        self.assertTrue(DockerProviderRuntimeRunner().health_check(runtime=runtime).recovery_recommended)
        run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="secret daemon details")
        result = DockerProviderRuntimeRunner().health_check(runtime=runtime)
        self.assertTrue(result.recovery_recommended)
        self.assertNotIn("secret", result.reason)

    @patch("apps.providers.runtime_runner.wait_for_runtime_port")
    @patch("apps.providers.runtime_runner._docker_host_port", return_value=29080)
    @patch("apps.providers.runtime_runner._run_docker")
    @patch("apps.providers.runtime_runner._container_is_running", return_value=True)
    @patch("apps.providers.runtime_runner._container_has_managed_restart_policy", return_value=True)
    @patch("apps.providers.runtime_runner._provider_runtime_container_id", return_value="exact-managed-container")
    def test_wedged_proxy_restarts_exact_container_and_refreshes_port(self, _id, _policy, _running, run, _port, wait):
        runtime = SimpleNamespace(runtime_type="codex_proxy")
        runner = DockerProviderRuntimeRunner()
        with patch.object(runner, "health_check", return_value=ProviderRuntimeHealthResult(False, False, "Runtime transport is unavailable.", True)):
            result = runner.restore(runtime=runtime, proxy_api_key="stable-key")
        run.assert_called_once_with(["docker", "restart", "exact-managed-container"], timeout=30)
        self.assertEqual(result.container_id, "exact-managed-container")
        self.assertEqual(result.internal_api_url, "http://127.0.0.1:29080/v1")
        wait.assert_called_once()
