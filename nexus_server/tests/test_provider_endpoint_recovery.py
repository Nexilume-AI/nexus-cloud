import json
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from django.test import SimpleTestCase, override_settings

from apps.providers import runtime_runner as rr


def snapshot(total=1, authenticated=True):
    return {"status": "ok", "authenticated": authenticated, "pool": {"total": total}}


class ProviderEndpointRecoveryTests(SimpleTestCase):
    def setUp(self):
        self.runtime = SimpleNamespace(
            runtime_type="codex_proxy", container_id="managed-container",
            internal_api_url="http://127.0.0.1:1115/v1", encrypted_proxy_api_key="",
        )

    def probe(self, host, container):
        with patch.object(rr, "_codex_host_health", side_effect=host), patch.object(
            rr, "_codex_container_health", side_effect=container
        ):
            return rr.probe_codex_endpoint_health(runtime=self.runtime)

    def test_persistent_wrong_endpoint_is_recoverable_not_login_required(self):
        result = self.probe([snapshot(0, False)] * 2, [snapshot()] * 2)
        self.assertFalse(result.healthy)
        self.assertFalse(result.login_required)
        self.assertTrue(result.recovery_recommended)
        self.assertIn("PROVIDER_ENDPOINT_MISMATCH", result.reason)

    def test_transient_login_change_does_not_restart(self):
        result = self.probe([snapshot(0, False), snapshot()], [snapshot()] * 2)
        self.assertTrue(result.healthy)
        self.assertFalse(result.recovery_recommended)

    def test_matching_missing_login_requests_sign_in_not_restart(self):
        result = self.probe([snapshot(0, False)], [snapshot(0, False)])
        self.assertTrue(result.login_required)
        self.assertFalse(result.recovery_recommended)

    def test_quota_or_cooldown_is_not_misclassified_as_lost_login(self):
        result = self.probe([snapshot(1, False)], [snapshot(1, False)])
        self.assertFalse(result.login_required)
        self.assertFalse(result.recovery_recommended)
        self.assertIn("quota", result.reason)

    def test_unknown_container_state_never_authorizes_destructive_recovery(self):
        result = self.probe([snapshot(0, False)] * 2, [None] * 2)
        self.assertFalse(result.healthy)
        self.assertFalse(result.login_required)
        self.assertFalse(result.recovery_recommended)

    def test_unknown_host_state_never_claims_login_failure(self):
        result = self.probe([None] * 2, [snapshot()] * 2)
        self.assertFalse(result.healthy)
        self.assertFalse(result.login_required)
        self.assertFalse(result.recovery_recommended)

    def test_confirmed_transport_failure_remains_recoverable(self):
        result = self.probe([{"transport_unavailable": True}] * 2, [snapshot()] * 2)
        self.assertTrue(result.recovery_recommended)
        self.assertFalse(result.login_required)

    def test_slow_endpoint_gets_confirmation_without_restart(self):
        result = self.probe([{"transport_unavailable": True}, snapshot()], [snapshot()] * 2)
        self.assertTrue(result.healthy)
        self.assertFalse(result.recovery_recommended)

    def test_slow_docker_probe_gets_patient_confirmation(self):
        with patch.object(rr, "_codex_host_health", return_value=snapshot()) as host, patch.object(
            rr, "_codex_container_health", side_effect=[None, snapshot()]
        ) as container:
            result = rr.probe_codex_endpoint_health(runtime=self.runtime)
        self.assertTrue(result.healthy)
        self.assertFalse(result.recovery_recommended)
        self.assertEqual([c.kwargs["timeout"] for c in container.call_args_list], [5, 15])
        self.assertEqual(host.call_args.kwargs["timeout"], 15)

    def test_changed_mismatch_is_not_confirmed(self):
        result = self.probe([snapshot(0, False), snapshot(2)], [snapshot(), snapshot()])
        self.assertFalse(result.healthy)
        self.assertFalse(result.recovery_recommended)

    def test_invalid_health_payloads_are_rejected(self):
        for payload in (None, [], {}, snapshot(-1), snapshot(True), snapshot(1, "true")):
            self.assertIsNone(rr._codex_health_summary(payload))
        self.assertEqual(rr._codex_health_summary(snapshot()), (1, True))

    @patch.object(rr, "build_opener")
    def test_host_probe_is_bounded_unauthenticated_and_direct(self, build):
        response = build.return_value.open.return_value.__enter__.return_value
        response.read.return_value = json.dumps(snapshot()).encode()
        self.assertEqual(rr._codex_host_health(runtime=self.runtime), snapshot())
        request = build.return_value.open.call_args.args[0]
        self.assertFalse(request.has_header("Authorization"))
        self.assertEqual(request.full_url, "http://127.0.0.1:1115/health")
        response.read.assert_called_once_with(4097)
        response.read.return_value = b"x" * 4097
        self.assertIsNone(rr._codex_host_health(runtime=self.runtime))

    @patch.object(rr, "subprocess")
    def test_container_probe_returns_only_bounded_health_not_secrets(self, subprocess):
        subprocess.run.return_value = SimpleNamespace(returncode=0, stdout=json.dumps(snapshot()))
        self.assertEqual(rr._codex_container_health(runtime=self.runtime), snapshot())
        command = subprocess.run.call_args.args[0]
        self.assertEqual(command[:4], ["docker", "exec", "managed-container", "node"])
        self.assertNotIn("PROXY_API_KEY", command[-1])
        self.assertIn("127.0.0.1:8080/health", command[-1])
        subprocess.run.return_value.stdout = "x" * 4097
        self.assertIsNone(rr._codex_container_health(runtime=self.runtime))

    def test_confirmed_mismatch_recreates_only_target_preserving_key(self):
        runner = rr.DockerProviderRuntimeRunner()
        result = rr.ProviderRuntimeStartResult("new-container", "http://localhost/login", "http://localhost/v1")
        with patch.object(rr.CodexProxyRuntimeAdapter, "prepare_storage", return_value=False), patch.object(
            rr, "approved_release", return_value=None
        ), patch.object(
            rr, "_provider_runtime_container_id", return_value="managed-container"
        ), patch.object(rr, "_container_has_managed_restart_policy", return_value=True), patch.object(
            rr, "_container_is_running", return_value=True
        ), patch.object(rr, "uses_container_network_endpoints", return_value=False), patch.object(
            rr, "_docker_host_port", return_value=1115
        ), patch.object(runner, "health_check", return_value=rr.ProviderRuntimeHealthResult(
            False, False, "PROVIDER_ENDPOINT_MISMATCH: inconsistent endpoint", True
        )), patch.object(runner, "start", return_value=result) as start, patch.object(rr, "_run_docker") as run:
            self.assertEqual(runner.restore(runtime=self.runtime, proxy_api_key="preserved-key"), result)
        start.assert_called_once_with(runtime=self.runtime, proxy_api_key="preserved-key")
        run.assert_not_called()

    @patch.object(rr, "subprocess")
    @patch.object(rr, "probe_openai_compatible_health")
    @patch.object(rr, "probe_codex_endpoint_health")
    def test_http_200_cannot_hide_endpoint_mismatch(self, codex, http, subprocess):
        subprocess.run.return_value = SimpleNamespace(returncode=0, stdout="true")
        codex.return_value = rr.ProviderRuntimeHealthResult(False, False, "PROVIDER_ENDPOINT_MISMATCH", True)
        result = rr.DockerProviderRuntimeRunner().health_check(runtime=self.runtime)
        self.assertTrue(result.recovery_recommended)
        http.assert_not_called()

    @override_settings(NEXUS_PROVIDER_RUNTIME_RUNNER="controller")
    def test_manual_refresh_uses_recovered_endpoint_and_never_restarts_stopped_runtime(self):
        from apps.providers.runtime_services import _prepare_manual_catalog_refresh
        runtime = SimpleNamespace(id="runtime", runtime_type="codex_proxy", status="active")
        recovered = SimpleNamespace(status="active", internal_api_url="http://localhost:30000/v1")
        with patch("apps.providers.runtime_services.reconcile_provider_runtime_health", return_value=recovered) as reconcile:
            self.assertIs(_prepare_manual_catalog_refresh(runtime), recovered)
            reconcile.assert_called_once_with(runtime_id="runtime")
            reconcile.reset_mock()
            runtime.status = "stopped"
            self.assertIs(_prepare_manual_catalog_refresh(runtime), runtime)
            reconcile.assert_not_called()

    @override_settings(NEXUS_PROVIDER_RUNTIME_RUNNER="controller")
    def test_manual_refresh_preserves_specific_recovery_reason(self):
        from apps.providers.runtime_services import _prepare_manual_catalog_refresh, ProviderRuntimeError
        runtime = SimpleNamespace(id="runtime", runtime_type="codex_proxy", status="active")
        recovered = SimpleNamespace(status="login_required", last_error="Sign in to this Provider.")
        with patch("apps.providers.runtime_services.reconcile_provider_runtime_health", return_value=recovered):
            with self.assertRaisesMessage(ProviderRuntimeError, "Sign in to this Provider."):
                _prepare_manual_catalog_refresh(runtime)

    def test_auth_state_alone_is_not_proof_of_wrong_port(self):
        result = self.probe([snapshot(1, False)] * 2, [snapshot()] * 2)
        self.assertFalse(result.healthy)
        self.assertFalse(result.recovery_recommended)

    def test_health_redirect_is_not_followed(self):
        self.assertIsNone(rr._NoHealthRedirect().redirect_request(None, None, 302, "", {}, "https://other.test"))


class ProviderCatalogReliabilityTests(SimpleTestCase):
    def setUp(self):
        self.runtime = SimpleNamespace(
            runtime_type="codex_proxy", internal_api_url="http://127.0.0.1:1115/v1",
            encrypted_proxy_api_key="", _discovered_model_contracts={},
        )

    def response(self, body):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = body
        return response

    @patch("apps.providers.runtime_services.urlopen")
    def test_catalog_retries_timeout_once_and_recovers(self, open_url):
        from apps.providers.runtime_services import discover_runtime_model_ids
        open_url.side_effect = [TimeoutError(), self.response(b'{"data":[{"id":"model-a"}]}')]
        self.assertEqual(discover_runtime_model_ids(runtime=self.runtime), ["model-a"])
        self.assertEqual(open_url.call_count, 2)

    @patch("apps.providers.runtime_services.urlopen")
    def test_catalog_error_categories_and_retry_bounds(self, open_url):
        from apps.providers.runtime_services import discover_runtime_model_ids, ProviderRuntimeError
        for error, code, attempts in (
            (TimeoutError("secret"), "PROVIDER_CATALOG_TIMEOUT", 2),
            (URLError("secret"), "PROVIDER_CATALOG_CONNECTION_FAILED", 2),
            (HTTPError("private", 401, "secret", {}, None), "PROVIDER_LOGIN_REQUIRED", 1),
            (HTTPError("private", 403, "secret", {}, None), "PROVIDER_CATALOG_FORBIDDEN", 1),
            (HTTPError("private", 503, "secret", {}, None), "PROVIDER_CATALOG_HTTP_503", 2),
        ):
            with self.subTest(code=code):
                open_url.reset_mock()
                open_url.side_effect = error
                with self.assertRaisesMessage(ProviderRuntimeError, code) as caught:
                    discover_runtime_model_ids(runtime=self.runtime)
                self.assertNotIn("secret", str(caught.exception))
                if isinstance(error, HTTPError) and error.code == 403:
                    self.assertNotIn("PROVIDER_LOGIN_REQUIRED", str(caught.exception))
                self.assertEqual(open_url.call_count, attempts)

    @patch("apps.providers.runtime_services.urlopen")
    def test_invalid_json_is_not_retried_or_confused_with_transport(self, open_url):
        from apps.providers.runtime_services import discover_runtime_model_ids, ProviderRuntimeError
        open_url.return_value = self.response(b'<html>private error</html>')
        with self.assertRaisesMessage(ProviderRuntimeError, "PROVIDER_CATALOG_INVALID_JSON"):
            discover_runtime_model_ids(runtime=self.runtime)
        self.assertEqual(open_url.call_count, 1)

    @patch("apps.providers.runtime_services.urlopen", side_effect=TimeoutError("secret"))
    def test_inference_timeout_is_unknown_and_never_replayed(self, open_url):
        from apps.providers.runtime_services import probe_runtime_model
        healthy, reason = probe_runtime_model(runtime=self.runtime, upstream_model_id="model-a")
        self.assertIsNone(healthy)
        self.assertIn("PROVIDER_MODEL_TIMEOUT", reason)
        self.assertNotIn("secret", reason)
        self.assertEqual(open_url.call_count, 1)

    @patch.object(rr, "build_opener")
    def test_cliproxy_expired_auth_is_sign_in_not_empty_catalog(self, build):
        self.runtime.encrypted_proxy_api_key = "encrypted"
        build.return_value.open.return_value = self.response(json.dumps({"files": [
            {"provider": "codex", "status": "error", "unavailable": True,
             "status_message": "credential expired private details"},
        ]}).encode())
        with patch.object(rr, "decrypt_secret", return_value="secret"):
            result = rr.probe_cliproxyapi_auth_health(runtime=self.runtime)
        self.assertTrue(result.login_required)
        self.assertFalse(result.recovery_recommended)
        self.assertNotIn("private", result.reason)

    def test_probe_uncertainty_does_not_claim_healthy_or_destroy_history(self):
        from apps.providers.runtime_services import _apply_model_probe
        from django.utils import timezone
        offer = SimpleNamespace(health_status="healthy", health_reason="old", metadata={})
        _apply_model_probe(offer, (None, "PROVIDER_MODEL_TIMEOUT: retry scheduled."), timezone.now())
        self.assertEqual(offer.health_status, "degraded")
        self.assertEqual(offer.metadata["health_probe"]["last_definitive_status"], "healthy")
        self.assertEqual(offer.metadata["health_probe"]["state"], "pending")
        _apply_model_probe(offer, (None, "Still pending."), timezone.now())
        self.assertEqual(offer.health_status, "degraded")
        from datetime import timedelta
        _apply_model_probe(offer, (None, "Still pending."), timezone.now() + timedelta(minutes=6))
        # A transient check still cannot erase the last verified model state.
        self.assertEqual(offer.health_status, "degraded")
        _apply_model_probe(offer, (True, "Verified."), timezone.now())
        self.assertEqual(offer.health_status, "healthy")
        self.assertEqual(offer.metadata["health_probe"]["state"], "verified")

    @patch.object(rr, "build_opener")
    def test_cliproxy_mixed_accounts_quota_and_unknown_are_not_expired(self, build):
        self.runtime.encrypted_proxy_api_key = "encrypted"
        cases = [
            ([{"status": "active"}, {"status": "error", "status_message": "expired"}], True, False),
            ([{"status": "error", "unavailable": True, "status_message": "quota exceeded"}], False, False),
            ([], False, True),
        ]
        with patch.object(rr, "decrypt_secret", return_value="secret"):
            for rows, healthy, login in cases:
                build.return_value.open.return_value = self.response(json.dumps({"files": rows}).encode())
                result = rr.probe_cliproxyapi_auth_health(runtime=self.runtime)
                self.assertEqual((result.healthy, result.login_required), (healthy, login))
                self.assertFalse(result.recovery_recommended)
            build.return_value.open.return_value = self.response(b'not-json')
            result = rr.probe_cliproxyapi_auth_health(runtime=self.runtime)
            self.assertTrue(result.inconclusive)
            self.assertFalse(result.login_required)

    @patch("apps.providers.runtime_services.urlopen")
    def test_catalog_size_limit_retains_directory_without_retry(self, open_url):
        from apps.providers.runtime_services import discover_runtime_model_ids, ProviderRuntimeError
        open_url.return_value = self.response(b'x' * (2 * 1024 * 1024 + 1))
        with self.assertRaisesMessage(ProviderRuntimeError, "PROVIDER_CATALOG_TOO_LARGE"):
            discover_runtime_model_ids(runtime=self.runtime)
        self.assertEqual(open_url.call_count, 1)

    def test_controller_roundtrip_preserves_inconclusive_and_old_results(self):
        from apps.providers import provider_controller as pc
        health = rr.ProviderRuntimeHealthResult(False, False, "Check pending.", inconclusive=True)
        with patch.object(pc.ProviderRuntimeAccount, "objects") as objects, patch.object(pc, "DockerProviderRuntimeRunner") as runner:
            objects.select_related.return_value.get.return_value = self.runtime
            runner.return_value.health_check.return_value = health
            payload = pc.dispatch_provider_runtime_command({"action": "health", "runtime_id": "runtime"})
        self.assertTrue(payload["inconclusive"])
        self.assertTrue(rr.ProviderRuntimeHealthResult(**payload).inconclusive)
        payload.pop("inconclusive")
        self.assertFalse(rr.ProviderRuntimeHealthResult(**payload).inconclusive)
