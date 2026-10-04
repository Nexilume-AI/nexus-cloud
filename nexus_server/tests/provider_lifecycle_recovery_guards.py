"""Original Provider lifecycle recovery assertions shared by both hosts.

Hosts provide authenticated create_connection and owner/tenant fixtures. Process
and probe mocks remain the original test boundary, not live Docker acceptance.
"""
from datetime import timedelta
from unittest.mock import patch
from django.test import override_settings
from django.core.cache import cache
from django.utils import timezone
from apps.common.crypto import decrypt_secret, encrypt_secret
from apps.deployments.models import CanonicalModel, Deployment
from apps.providers.models import ProviderAccount, ProviderRuntimeAccount, ProviderRuntimeModelOffer
from apps.providers.runtime_services import (
    ProviderRuntimeError, reconcile_provider_runtime_health,
    reconcile_stale_provider_runtime_starts, reconcile_stale_provider_runtime_stops,
    restore_provider_runtime_processes,
)
from apps.providers.runtime_runner import ProviderRuntimeHealthResult, ProviderRuntimeStartResult
from apps.providers.tasks import refresh_active_provider_runtime_models, reconcile_provider_runtime_health_task


class ProviderLifecycleRecoveryGuards:
    def test_verified_release_daemon_outage_at_initial_start_is_automatically_adopted_after_recovery(self):
        import json
        from types import SimpleNamespace
        from apps.providers import runtime_release, runtime_runner

        created = self.create_connection(engine="codex_proxy", upstream="openai")
        runtime = ProviderAccount.objects.get(id=created["id"]).source_runtime_accounts.get()
        image_id = "sha256:" + "a" * 64
        receipt = {"image_id": image_id, "labels": {"io.nexilume.provider": "codex_proxy"}}
        runner = runtime_runner.DockerProviderRuntimeRunner()

        def health(*, runtime):
            present = bool(runtime.container_id)
            return ProviderRuntimeHealthResult(present, False, "ready" if present else "container is not running", not present)

        with patch("apps.providers.runtime_services.get_provider_runtime_runner", return_value=runner), \
             patch.object(runtime_runner, "approved_release", return_value=receipt), \
             patch.object(runtime_release.subprocess, "run") as docker, \
             patch.object(runtime_runner.CodexProxyRuntimeAdapter, "prepare_storage", return_value=False) as prepare, \
             patch.object(runtime_runner, "_provider_runtime_container_id", return_value="a" * 64), \
             patch.object(runtime_runner, "_container_has_managed_restart_policy", return_value=True), \
             patch.object(runtime_runner, "_container_is_running", return_value=True), \
             patch.object(runtime_runner, "uses_container_network_endpoints", return_value=True), \
             patch.object(runtime_runner, "wait_for_runtime_port"), \
             patch.object(runtime_runner, "_run_docker") as mutation, \
             patch.object(runner, "health_check", side_effect=health), \
             patch.object(runner, "start", wraps=runner.start) as start, \
             patch.object(runner, "restore", wraps=runner.restore) as restore:
            docker.return_value = SimpleNamespace(returncode=1, stdout="", stderr="Cannot connect to the Docker daemon. private fixture path")
            response = self.request("post", f"/api/v1/provider-connections/{created['id']}/start/")
            self.assertEqual(response.status_code, 400)
            runtime.refresh_from_db()
            self.assertTrue(runtime.last_error.startswith("Automatic start recovery failed:"))
            self.assertNotIn("private", response.content.decode())
            preserved_key = runtime.encrypted_proxy_api_key
            prepare.assert_not_called()
            mutation.assert_not_called()
            docker.side_effect = [
                SimpleNamespace(returncode=0, stdout=json.dumps([{"Id": image_id, "Config": {"Labels": receipt["labels"]}}]), stderr=""),
                SimpleNamespace(returncode=0, stdout=json.dumps([{"Image": image_id}]), stderr=""),
            ]

            self.assertEqual(reconcile_provider_runtime_health_task()["healthy"], 1)

            runtime.refresh_from_db()
            self.assertEqual(runtime.status, ProviderRuntimeAccount.STATUS_ACTIVE)
            self.assertEqual(runtime.container_id, "a" * 64)
            self.assertEqual(runtime.encrypted_proxy_api_key, preserved_key)
            start.assert_called_once()
            restore.assert_called_once()
            mutation.assert_not_called()

    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_busy_start_is_retryable_without_replaying_a_completed_controller_operation(self, get_runner):
        from apps.providers.provider_controller import ProviderRuntimeControllerBusy

        created = self.create_connection(engine="codex_proxy", upstream="openai")
        runtime = ProviderAccount.objects.get(id=created["id"]).source_runtime_accounts.get()
        runner = get_runner.return_value
        runner.start.side_effect = ProviderRuntimeControllerBusy("Provider Runtime operation is busy; retry scheduled.")
        response = self.request("post", f"/api/v1/provider-connections/{created['id']}/start/")
        self.assertEqual(response.status_code, 400)
        runtime.refresh_from_db()
        self.assertTrue(runtime.last_error.startswith("Automatic start recovery failed:"))
        runner.health_check.return_value = ProviderRuntimeHealthResult(True, False, "Existing operation completed.")

        self.assertEqual(reconcile_provider_runtime_health_task()["healthy"], 1)
        runtime.refresh_from_db()
        self.assertEqual(runtime.status, ProviderRuntimeAccount.STATUS_ACTIVE)
        runner.start.assert_called_once()
        runner.restore.assert_not_called()

    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_transient_controller_start_failure_is_recovered_by_normal_maintenance(self, get_runner):
        from apps.providers.provider_controller import ProviderRuntimeControllerUnavailable

        created = self.create_connection(engine="codex_proxy", upstream="openai")
        runtime = ProviderAccount.objects.get(id=created["id"]).source_runtime_accounts.get()
        runner = get_runner.return_value
        runner.start.side_effect = ProviderRuntimeControllerUnavailable("private-detail")
        response = self.request("post", f"/api/v1/provider-connections/{created['id']}/start/")
        self.assertEqual(response.status_code, 400)
        runtime.refresh_from_db()
        self.assertEqual(runtime.status, ProviderRuntimeAccount.STATUS_FAILED)
        self.assertTrue(runtime.last_error.startswith("Automatic start recovery failed:"))
        self.assertNotIn("private-detail", runtime.last_error)
        preserved_key = runtime.encrypted_proxy_api_key
        runner.health_check.return_value = ProviderRuntimeHealthResult(False, False, "container is stopped", True)
        runner.restore.return_value = ProviderRuntimeStartResult(
            "restored-container", "http://runtime/login", "http://runtime/v1",
            health=ProviderRuntimeHealthResult(True, False, "ready"),
        )

        result = reconcile_provider_runtime_health_task()

        self.assertEqual(result["healthy"], 1)
        runtime.refresh_from_db()
        self.assertEqual(runtime.status, ProviderRuntimeAccount.STATUS_ACTIVE)
        self.assertEqual(runtime.encrypted_proxy_api_key, preserved_key)
        runner.start.assert_called_once()
        runner.restore.assert_called_once()

    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_start_configuration_failure_is_safe_and_never_automatically_retried(self, get_runner):
        from rest_framework.exceptions import APIException

        created = self.create_connection(engine="codex_proxy", upstream="openai")
        runtime = ProviderAccount.objects.get(id=created["id"]).source_runtime_accounts.get()
        runner = get_runner.return_value
        safe = "Provider release approval is missing or invalid; install a verified release receipt."
        for failure, expected in (
            (APIException(safe), safe),
            (RuntimeError("Bearer private-detail"), "Provider Runtime start failed. Review runtime configuration and controller availability."),
        ):
            with self.subTest(category=type(failure).__name__):
                runner.start.side_effect = failure
                response = self.request("post", f"/api/v1/provider-connections/{created['id']}/start/")
                self.assertEqual(response.status_code, 400)
                runtime.refresh_from_db()
                self.assertEqual(runtime.status, ProviderRuntimeAccount.STATUS_FAILED)
                self.assertEqual(runtime.last_error, expected)
                self.assertNotIn("private-detail", response.content.decode())
                self.assertEqual(reconcile_provider_runtime_health_task()["examined"], 0)
                runner.health_check.assert_not_called()
                runner.restore.assert_not_called()

    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_transient_start_recovery_never_resurrects_stopped_or_deleted_runtime(self, get_runner):
        from apps.common.models import SoftDeleteModel
        from apps.providers.provider_controller import ProviderRuntimeControllerUnavailable

        runner = get_runner.return_value
        runner.start.side_effect = ProviderRuntimeControllerUnavailable("temporary transport outage")
        for lifecycle in (ProviderRuntimeAccount.STATUS_STOPPED, SoftDeleteModel.STATUS_DELETED):
            with self.subTest(lifecycle=lifecycle):
                created = self.create_connection(name=f"Recovery {lifecycle}", engine="codex_proxy", upstream="openai")
                runtime = ProviderAccount.objects.get(id=created["id"]).source_runtime_accounts.get()
                response = self.request("post", f"/api/v1/provider-connections/{created['id']}/start/")
                self.assertEqual(response.status_code, 400)
                ProviderRuntimeAccount.objects.filter(id=runtime.id).update(status=lifecycle)

                self.assertEqual(reconcile_provider_runtime_health_task()["examined"], 0)
                runtime.refresh_from_db()
                self.assertEqual(runtime.status, lifecycle)
                runner.health_check.assert_not_called()
                runner.restore.assert_not_called()

    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_background_health_reconciliation_marks_failure_and_recovers(self, get_runner):
        created = self.create_connection(engine="codex_proxy", upstream="openai")
        runtime = ProviderAccount.objects.get(id=created["id"]).source_runtime_accounts.get()
        runtime.status = ProviderRuntimeAccount.STATUS_ACTIVE
        runtime.save(update_fields=["status", "updated_at"])
        get_runner.return_value.health_check.return_value = ProviderRuntimeHealthResult(
            healthy=False,
            login_required=False,
            reason="runtime unreachable",
        )

        failed = reconcile_provider_runtime_health(runtime_id=runtime.id)

        self.assertEqual(failed.status, ProviderRuntimeAccount.STATUS_UNHEALTHY)
        self.assertIn("runtime unreachable", failed.last_error)
        get_runner.return_value.health_check.return_value = ProviderRuntimeHealthResult(
            healthy=True,
            login_required=False,
            reason="ready",
        )

        recovered = reconcile_provider_runtime_health(runtime_id=runtime.id)

        self.assertEqual(recovered.status, ProviderRuntimeAccount.STATUS_ACTIVE)
        self.assertEqual(recovered.last_error, "")

    @patch("apps.providers.runtime_services.refresh_runtime_model_offers")
    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_background_health_restarts_a_lost_managed_container(self, get_runner, refresh_models):
        created = self.create_connection(engine="codex_proxy", upstream="openai")
        runtime = ProviderAccount.objects.get(id=created["id"]).source_runtime_accounts.get()
        runtime.status = ProviderRuntimeAccount.STATUS_ACTIVE
        runtime.container_id = "stopped-container"
        runtime.internal_api_url = "http://127.0.0.1:19080/v1"
        runtime.encrypted_proxy_api_key = encrypt_secret("stable-recovery-key")
        runtime.save(update_fields=[
            "status", "container_id", "internal_api_url", "encrypted_proxy_api_key", "updated_at",
        ])
        account = runtime.source_provider_account
        source = Deployment.objects.create(
            tenant=self.tenant, provider=account.provider, provider_account=account,
            provider_runtime=runtime, deployment_id="recovered-source",
            canonical_model=CanonicalModel.objects.create(key="recovery-model", display_name="Recovery Model"),
            upstream_model_id="recovery-model", endpoint=runtime.internal_api_url,
        )
        runner = get_runner.return_value
        runner.health_check.side_effect = [
            ProviderRuntimeHealthResult(
                healthy=False,
                login_required=False,
                reason="container is stopped",
                recovery_recommended=True,
            ),
            ProviderRuntimeHealthResult(healthy=True, login_required=False, reason="ready"),
        ]
        runner.restore.return_value = ProviderRuntimeStartResult(
            container_id="restored-container",
            internal_login_url="http://127.0.0.1:29080/login",
            internal_api_url="http://127.0.0.1:29080/v1",
        )

        recovered = reconcile_provider_runtime_health(runtime_id=runtime.id)

        recovered.refresh_from_db()
        self.assertEqual(recovered.status, ProviderRuntimeAccount.STATUS_ACTIVE)
        self.assertEqual(recovered.container_id, "restored-container")
        self.assertEqual(recovered.internal_api_url, "http://127.0.0.1:29080/v1")
        source.refresh_from_db()
        self.assertEqual(source.endpoint, recovered.internal_api_url)
        runner.restore.assert_called_once()
        refresh_models.assert_not_called()
        self.assertEqual(cache.get(f"nexus:providers:catalog:{runtime.id}")["next_at"], 0)
        self.assertEqual(decrypt_secret(recovered.encrypted_proxy_api_key), "stable-recovery-key")

    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_background_health_does_not_restart_for_upstream_http_failure(self, get_runner):
        created = self.create_connection(engine="codex_proxy", upstream="openai")
        runtime = ProviderAccount.objects.get(id=created["id"]).source_runtime_accounts.get()
        runtime.status = ProviderRuntimeAccount.STATUS_ACTIVE
        runtime.encrypted_proxy_api_key = encrypt_secret("stable-recovery-key")
        runtime.save(update_fields=["status", "encrypted_proxy_api_key", "updated_at"])
        get_runner.return_value.health_check.return_value = ProviderRuntimeHealthResult(
            healthy=False,
            login_required=False,
            reason="runtime returned HTTP 503",
            recovery_recommended=False,
        )

        failed = reconcile_provider_runtime_health(runtime_id=runtime.id)

        self.assertEqual(failed.status, ProviderRuntimeAccount.STATUS_UNHEALTHY)
        get_runner.return_value.restore.assert_not_called()

    @patch("apps.providers.runtime_services.refresh_runtime_model_offers", side_effect=ProviderRuntimeError("catalog unavailable"))
    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_cloud_restart_keeps_healthy_runtime_active_when_catalog_refresh_fails(
        self,
        get_runner,
        _refresh_models,
    ):
        created = self.create_connection(engine="codex_proxy", upstream="openai")
        runtime = ProviderAccount.objects.get(id=created["id"]).source_runtime_accounts.get()
        runtime.status = ProviderRuntimeAccount.STATUS_ACTIVE
        runtime.container_id = "running-container"
        runtime.internal_api_url = "http://127.0.0.1:19080/v1"
        runtime.encrypted_proxy_api_key = encrypt_secret("stable-recovery-key")
        runtime.save(update_fields=[
            "status", "container_id", "internal_api_url", "encrypted_proxy_api_key", "updated_at",
        ])
        get_runner.return_value.health_check.return_value = ProviderRuntimeHealthResult(
            healthy=True,
            login_required=False,
            reason="ready",
        )

        result = restore_provider_runtime_processes()

        runtime.refresh_from_db()
        self.assertEqual(result["failed"], 0)
        self.assertEqual(runtime.status, ProviderRuntimeAccount.STATUS_ACTIVE)
        self.assertEqual(runtime.last_error, "")

    @patch("apps.providers.tasks.refresh_runtime_model_offers", side_effect=ProviderRuntimeError("catalog unavailable"))
    def test_periodic_catalog_failure_preserves_last_known_healthy_offers(self, _refresh_models):
        created = self.create_connection()
        runtime = ProviderAccount.objects.get(id=created["id"]).source_runtime_accounts.get()
        runtime.status = ProviderRuntimeAccount.STATUS_ACTIVE
        runtime.save(update_fields=["status", "updated_at"])
        offer = ProviderRuntimeModelOffer.objects.create(
            runtime_account=runtime,
            upstream_model_id="last-known-good-model",
            health_status=ProviderRuntimeModelOffer.HEALTH_HEALTHY,
            health_reason="Last successful probe.",
            last_discovered_at=timezone.now() - timedelta(hours=1),
        )

        result = refresh_active_provider_runtime_models()

        offer.refresh_from_db()
        self.assertEqual(result["failed"], 1)
        self.assertEqual(offer.health_status, ProviderRuntimeModelOffer.HEALTH_HEALTHY)
        self.assertEqual(offer.health_reason, "Last successful probe.")
        second = refresh_active_provider_runtime_models()
        self.assertEqual(second["failed"], 0)
        _refresh_models.assert_called_once()

    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_failed_recovery_backs_off_and_never_persists_exception_secrets(self, get_runner):
        runtime = ProviderAccount.objects.get(id=self.create_connection(engine="codex_proxy")["id"]).source_runtime_accounts.get()
        runtime.status = ProviderRuntimeAccount.STATUS_ACTIVE
        runtime.encrypted_proxy_api_key = encrypt_secret("stable-key")
        runtime.save()
        runner = get_runner.return_value
        runner.health_check.return_value = ProviderRuntimeHealthResult(False, False, "container is stopped", True)
        runner.restore.side_effect = RuntimeError("Bearer secret-must-not-leak")
        reconcile_provider_runtime_health(runtime_id=runtime.id)
        runtime.refresh_from_db()
        self.assertIn("during container restore", runtime.last_error)
        reconcile_provider_runtime_health(runtime_id=runtime.id)
        self.assertEqual(runner.restore.call_count, 1)
        runtime.refresh_from_db()
        self.assertNotIn("secret-must-not-leak", runtime.last_error)
        key = f"nexus:providers:runtime-recovery:{runtime.id}"
        cache.delete(key)
        reconcile_provider_runtime_health(runtime_id=runtime.id)
        self.assertEqual(runner.restore.call_count, 2)
        self.assertEqual(cache.get(key + ":attempts"), 2)

    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_inconclusive_endpoint_check_preserves_models_and_recovers(self, get_runner):
        runtime = ProviderAccount.objects.get(id=self.create_connection(engine="codex_proxy")["id"]).source_runtime_accounts.get()
        runtime.status = ProviderRuntimeAccount.STATUS_ACTIVE
        runtime.save()
        offer = ProviderRuntimeModelOffer.objects.create(
            runtime_account=runtime, upstream_model_id="known-model", health_status="healthy",
            health_reason="Last verified model result.",
        )
        runner = get_runner.return_value
        runner.health_check.return_value = ProviderRuntimeHealthResult(
            False, False, "PROVIDER_ENDPOINT_CHECK_PENDING: Retry scheduled.", inconclusive=True,
        )
        pending = reconcile_provider_runtime_health(runtime_id=runtime.id)
        self.assertEqual(pending.status, "unhealthy")
        offer.refresh_from_db()
        self.assertEqual(offer.health_status, "healthy")
        self.assertEqual(offer.health_reason, "Last verified model result.")
        runner.restore.assert_not_called()
        runner.health_check.return_value = ProviderRuntimeHealthResult(True, False, "Verified.")
        recovered = reconcile_provider_runtime_health(runtime_id=runtime.id)
        self.assertEqual(recovered.status, "active")
        self.assertEqual(recovered.last_error, "")
        self.assertEqual(cache.get(f"nexus:providers:catalog:{runtime.id}")["next_at"], 0)

    @patch("apps.providers.runtime_services.probe_runtime_model", return_value=(None, "PROVIDER_MODEL_TIMEOUT: Check pending."))
    @patch("apps.providers.runtime_services.discover_runtime_model_ids", return_value=["known-model"])
    def test_partial_probe_commits_catalog_and_schedules_short_retry(self, discover, probe):
        runtime = ProviderAccount.objects.get(id=self.create_connection()["id"]).source_runtime_accounts.get()
        runtime.status = "active"
        runtime.save()
        offer = ProviderRuntimeModelOffer.objects.create(
            runtime_account=runtime, upstream_model_id="known-model", health_status="healthy",
            last_discovered_at=timezone.now() - timedelta(hours=1),
        )
        result = refresh_active_provider_runtime_models()
        self.assertEqual(result["failed"], 1)
        offer.refresh_from_db()
        self.assertEqual(offer.health_status, "degraded")
        self.assertEqual(offer.metadata["health_probe"]["state"], "pending")
        self.assertGreater(offer.last_discovered_at, timezone.now() - timedelta(minutes=1))
        state = cache.get(f"nexus:providers:catalog:{runtime.id}")
        import time
        self.assertLess(state["next_at"] - time.time(), 301)
        probe.assert_called_once()
        probe.return_value = (True, "Verified.")
        cache.delete(f"nexus:providers:catalog:{runtime.id}")
        from apps.providers.runtime_services import refresh_runtime_model_offers
        refresh_runtime_model_offers(runtime=runtime, strict=True)
        offer.refresh_from_db()
        self.assertEqual(offer.health_status, "healthy")

    @patch("apps.providers.runtime_runner.probe_cliproxyapi_auth_health")
    def test_expired_login_catalog_cannot_retire_text_models(self, auth):
        runtime = ProviderAccount.objects.get(id=self.create_connection(engine="cliproxyapi", upstream="openai")["id"]).source_runtime_accounts.get()
        runtime.status = "active"
        runtime.save()
        offer = ProviderRuntimeModelOffer.objects.create(
            runtime_account=runtime, upstream_model_id="known-model", health_status="healthy",
        )
        auth.return_value = ProviderRuntimeHealthResult(False, True, "PROVIDER_LOGIN_REQUIRED: Sign in again.")
        from apps.providers.runtime_services import refresh_runtime_model_offers
        with override_settings(NEXUS_PROVIDER_RUNTIME_RUNNER="controller"), patch(
            "apps.providers.runtime_services.urlopen"
        ) as network:
            with self.assertRaisesMessage(ProviderRuntimeError, "PROVIDER_LOGIN_REQUIRED"):
                refresh_runtime_model_offers(runtime=runtime, strict=True)
            network.assert_not_called()
        offer.refresh_from_db()
        self.assertNotEqual(offer.status, "unavailable")
        self.assertEqual(offer.health_status, "healthy")

    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_user_stop_wins_over_a_late_unhealthy_probe(self, get_runner):
        runtime = ProviderAccount.objects.get(id=self.create_connection(engine="codex_proxy")["id"]).source_runtime_accounts.get()
        runtime.status = ProviderRuntimeAccount.STATUS_ACTIVE
        runtime.encrypted_proxy_api_key = encrypt_secret("stable-key")
        runtime.save()

        def probe(**kwargs):
            ProviderRuntimeAccount.objects.filter(id=runtime.id).update(status=ProviderRuntimeAccount.STATUS_STOPPED)
            return ProviderRuntimeHealthResult(False, False, "container is stopped", True)

        get_runner.return_value.health_check.side_effect = probe
        recovered = reconcile_provider_runtime_health(runtime_id=runtime.id)
        self.assertEqual(recovered.status, ProviderRuntimeAccount.STATUS_STOPPED)
        get_runner.return_value.restore.assert_not_called()

    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_expired_login_is_not_fixed_by_restarting_container(self, get_runner):
        runtime = ProviderAccount.objects.get(id=self.create_connection(engine="codex_proxy")["id"]).source_runtime_accounts.get()
        runtime.status = ProviderRuntimeAccount.STATUS_ACTIVE
        runtime.save()
        get_runner.return_value.health_check.return_value = ProviderRuntimeHealthResult(False, True, "runtime returned HTTP 401")
        recovered = reconcile_provider_runtime_health(runtime_id=runtime.id)
        self.assertEqual(recovered.status, ProviderRuntimeAccount.STATUS_LOGIN_REQUIRED)
        runtime.source_provider_account.refresh_from_db()
        self.assertEqual(runtime.source_provider_account.login_status, ProviderAccount.LOGIN_REQUIRED)
        get_runner.return_value.restore.assert_not_called()

    @patch("apps.providers.tasks.refresh_runtime_model_offers")
    def test_catalog_success_keeps_normal_interval_and_failed_retry_can_recover(self, refresh_models):
        runtime = ProviderAccount.objects.get(id=self.create_connection()["id"]).source_runtime_accounts.get()
        runtime.status = ProviderRuntimeAccount.STATUS_ACTIVE
        runtime.save()
        refresh_models.side_effect = [ProviderRuntimeError("temporary"), []]
        self.assertEqual(refresh_active_provider_runtime_models()["failed"], 1)
        key = f"nexus:providers:catalog:{runtime.id}"
        state = cache.get(key)
        self.assertEqual(state["attempts"], 1)
        state["next_at"] = 0
        cache.set(key, state)
        self.assertEqual(refresh_active_provider_runtime_models()["refreshed"], 1)
        self.assertEqual(cache.get(key)["attempts"], 0)
        self.assertEqual(refresh_active_provider_runtime_models()["refreshed"], 0)
        self.assertEqual(refresh_models.call_count, 2)

    @patch("apps.providers.runtime_services.refresh_runtime_model_offers")
    def test_runtime_health_does_not_probe_every_model(self, refresh_models):
        created = self.create_connection()
        runtime = ProviderAccount.objects.get(id=created["id"]).source_runtime_accounts.get()
        runtime.status = ProviderRuntimeAccount.STATUS_ACTIVE
        runtime.save(update_fields=["status", "updated_at"])

        recovered = reconcile_provider_runtime_health(runtime_id=runtime.id)

        self.assertEqual(recovered.status, ProviderRuntimeAccount.STATUS_ACTIVE)
        refresh_models.assert_not_called()

    def test_direct_api_corrupt_credential_is_actionable_and_recovers_after_key_update(self):
        account = ProviderAccount.objects.get(id=self.create_connection()["id"])
        runtime = account.source_runtime_accounts.get()
        runtime.status = ProviderRuntimeAccount.STATUS_ACTIVE
        runtime.save()
        account.encrypted_key = "invalid-ciphertext"
        account.save()
        failed = reconcile_provider_runtime_health(runtime_id=runtime.id)
        self.assertEqual(failed.status, ProviderRuntimeAccount.STATUS_UNHEALTHY)
        self.assertIn("Re-enter the API key", failed.last_error)
        self.assertNotIn("invalid-ciphertext", failed.last_error)
        account.encrypted_key = encrypt_secret("replacement-key")
        account.save()
        recovered = reconcile_provider_runtime_health(runtime_id=runtime.id)
        self.assertEqual(recovered.status, ProviderRuntimeAccount.STATUS_ACTIVE)
        self.assertEqual(recovered.last_error, "")

    @override_settings(NEXUS_PROVIDER_LIFECYCLE_STALE_SECONDS=1, NEXUS_PROVIDER_RUNTIME_RUNNER="fake")
    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_interrupted_start_is_recovered_without_cloud_restart(self, get_runner):
        created = self.create_connection(engine="codex_proxy", upstream="openai")
        runtime = ProviderAccount.objects.get(id=created["id"]).source_runtime_accounts.get()
        ProviderRuntimeAccount.objects.filter(id=runtime.id).update(
            status=ProviderRuntimeAccount.STATUS_STARTING,
            encrypted_proxy_api_key=encrypt_secret("recovery-key"),
            updated_at=timezone.now() - timedelta(seconds=5),
        )
        get_runner.return_value.restore.return_value = ProviderRuntimeStartResult(
            container_id="recovered-container",
            internal_login_url="http://runtime/login",
            internal_api_url="http://runtime/v1",
        )
        get_runner.return_value.health_check.return_value = ProviderRuntimeHealthResult(
            healthy=True,
            login_required=False,
            reason="ready",
        )

        result = reconcile_stale_provider_runtime_starts()

        self.assertEqual(result, {"examined": 1, "recovered": 1, "failed": 0})
        runtime.refresh_from_db()
        self.assertEqual(runtime.status, ProviderRuntimeAccount.STATUS_ACTIVE)
        self.assertEqual(runtime.container_id, "recovered-container")

    @override_settings(NEXUS_PROVIDER_LIFECYCLE_STALE_SECONDS=1, NEXUS_PROVIDER_RUNTIME_RUNNER="fake")
    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_interrupted_start_uses_controller_health_for_restored_endpoint(self, get_runner):
        created = self.create_connection(engine="codex_proxy", upstream="openai")
        runtime = ProviderAccount.objects.get(id=created["id"]).source_runtime_accounts.get()
        ProviderRuntimeAccount.objects.filter(id=runtime.id).update(
            status=ProviderRuntimeAccount.STATUS_STARTING,
            container_id="old-container",
            internal_api_url="http://127.0.0.1:19080/v1",
            encrypted_proxy_api_key=encrypt_secret("recovery-key"),
            updated_at=timezone.now() - timedelta(seconds=5),
        )
        runner = get_runner.return_value
        runner.restore.return_value = ProviderRuntimeStartResult(
            container_id="restored-container",
            internal_login_url="http://127.0.0.1:29080/login",
            internal_api_url="http://127.0.0.1:29080/v1",
            health=ProviderRuntimeHealthResult(True, False, "Restored endpoint is ready."),
        )
        # A separate controller request reloads the old persisted endpoint until
        # the recovered container and URL have been committed by this function.
        runner.health_check.return_value = ProviderRuntimeHealthResult(
            False, False, "container is stopped", recovery_recommended=True,
        )

        result = reconcile_stale_provider_runtime_starts()

        self.assertEqual(result, {"examined": 1, "recovered": 1, "failed": 0})
        runtime.refresh_from_db()
        self.assertEqual(runtime.status, ProviderRuntimeAccount.STATUS_ACTIVE)
        self.assertEqual(runtime.container_id, "restored-container")
        self.assertEqual(runtime.internal_api_url, "http://127.0.0.1:29080/v1")
        runner.restore.assert_called_once()
        runner.health_check.assert_not_called()

    @override_settings(NEXUS_PROVIDER_LIFECYCLE_STALE_SECONDS=1, NEXUS_PROVIDER_RUNTIME_RUNNER="fake")
    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_failed_start_recovery_becomes_retryable(self, get_runner):
        from apps.providers.provider_controller import ProviderRuntimeControllerBusy, ProviderRuntimeControllerUnavailable
        created = self.create_connection(engine="codex_proxy", upstream="openai")
        runtime = ProviderAccount.objects.get(id=created["id"]).source_runtime_accounts.get()
        for category in (ProviderRuntimeControllerUnavailable, ProviderRuntimeControllerBusy):
            with self.subTest(category=category.__name__):
                ProviderRuntimeAccount.objects.filter(id=runtime.id).update(
                    status=ProviderRuntimeAccount.STATUS_STARTING,
                    encrypted_proxy_api_key=encrypt_secret("recovery-key"),
                    updated_at=timezone.now() - timedelta(seconds=5),
                )
                get_runner.return_value.restore.side_effect = category("private controller detail")

                result = reconcile_stale_provider_runtime_starts()

                self.assertEqual(result, {"examined": 1, "recovered": 0, "failed": 1})
                runtime.refresh_from_db()
                self.assertEqual(runtime.status, ProviderRuntimeAccount.STATUS_FAILED)
                self.assertIn("Automatic start recovery failed", runtime.last_error)
                self.assertNotIn("private controller detail", runtime.last_error)

    @override_settings(NEXUS_PROVIDER_LIFECYCLE_STALE_SECONDS=1, NEXUS_PROVIDER_RUNTIME_RUNNER="controller",
                       NEXUS_PROVIDER_RUNTIME_CONTROLLER_TIMEOUT_SECONDS=100,
                       NEXUS_PROVIDER_RUNTIME_BUILD_TIMEOUT_SECONDS=80,
                       NEXUS_PROVIDER_RUNTIME_START_ATTEMPTS=2,
                       NEXUS_PROVIDER_RUNTIME_START_DELAY_SECONDS=0.5)
    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_slow_start_is_not_recovered_until_permitted_controller_build_budget(self, get_runner):
        runtime = ProviderAccount.objects.get(id=self.create_connection(engine="codex_proxy")["id"]).source_runtime_accounts.get()
        ProviderRuntimeAccount.objects.filter(id=runtime.id).update(status="starting", encrypted_proxy_api_key=encrypt_secret("recovery-key"), updated_at=timezone.now() - timedelta(seconds=5))
        self.assertEqual(reconcile_stale_provider_runtime_starts()["examined"], 0)
        get_runner.return_value.restore.assert_not_called()
        ProviderRuntimeAccount.objects.filter(id=runtime.id).update(status="starting", updated_at=timezone.now() - timedelta(seconds=200))
        get_runner.return_value.restore.return_value = ProviderRuntimeStartResult(
            "restored-container", "http://runtime/login", "http://runtime/v1", health=ProviderRuntimeHealthResult(True, False, "ready"),
        )
        self.assertEqual(reconcile_stale_provider_runtime_starts()["recovered"], 1)

    @override_settings(NEXUS_PROVIDER_LIFECYCLE_STALE_SECONDS=1, NEXUS_PROVIDER_RUNTIME_RUNNER="fake")
    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_stale_start_claim_prevents_overlapping_restore_without_cache_lock(self, get_runner):
        runtime = ProviderAccount.objects.get(id=self.create_connection(engine="codex_proxy")["id"]).source_runtime_accounts.get()
        ProviderRuntimeAccount.objects.filter(id=runtime.id).update(status="starting", encrypted_proxy_api_key=encrypt_secret("recovery-key"), updated_at=timezone.now() - timedelta(seconds=5))

        def restore(**kwargs):
            self.assertEqual(reconcile_stale_provider_runtime_starts()["examined"], 0)
            return ProviderRuntimeStartResult("restored-container", "http://runtime/login", "http://runtime/v1", health=ProviderRuntimeHealthResult(True, False, "ready"))

        get_runner.return_value.restore.side_effect = restore
        self.assertEqual(reconcile_stale_provider_runtime_starts()["recovered"], 1)
        get_runner.return_value.restore.assert_called_once()

    @override_settings(NEXUS_PROVIDER_LIFECYCLE_STALE_SECONDS=1, NEXUS_PROVIDER_RUNTIME_RUNNER="fake")
    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_late_restore_does_not_overwrite_newer_starting_generation(self, get_runner):
        runtime = ProviderAccount.objects.get(id=self.create_connection(engine="codex_proxy")["id"]).source_runtime_accounts.get()
        ProviderRuntimeAccount.objects.filter(id=runtime.id).update(status="starting", encrypted_proxy_api_key=encrypt_secret("recovery-key"), updated_at=timezone.now() - timedelta(seconds=5))

        def restore(**kwargs):
            ProviderRuntimeAccount.objects.filter(id=runtime.id).update(container_id="newer-container", updated_at=timezone.now())
            return ProviderRuntimeStartResult("late-container", "http://late/login", "http://late/v1", health=ProviderRuntimeHealthResult(True, False, "ready"))

        get_runner.return_value.restore.side_effect = restore
        self.assertEqual(reconcile_stale_provider_runtime_starts()["recovered"], 0)
        runtime.refresh_from_db()
        self.assertEqual(runtime.status, "starting")
        self.assertEqual(runtime.container_id, "newer-container")
        get_runner.return_value.stop_restored_container.assert_not_called()

    @override_settings(NEXUS_PROVIDER_LIFECYCLE_STALE_SECONDS=1, NEXUS_PROVIDER_RUNTIME_RUNNER="fake")
    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_late_restore_cleans_exact_child_only_when_stopped_or_deleted(self, get_runner):
        from apps.common.models import SoftDeleteModel

        for lifecycle in ("stopped", SoftDeleteModel.STATUS_DELETED, "active", "starting"):
            with self.subTest(lifecycle=lifecycle):
                runtime = ProviderAccount.objects.get(id=self.create_connection(name=f"Late {lifecycle}", engine="codex_proxy")["id"]).source_runtime_accounts.get()
                ProviderRuntimeAccount.objects.filter(id=runtime.id).update(status="starting", encrypted_proxy_api_key=encrypt_secret("recovery-key"), updated_at=timezone.now() - timedelta(seconds=5))
                runner = get_runner.return_value
                runner.reset_mock()

                def restore(**kwargs):
                    ProviderRuntimeAccount.objects.filter(id=runtime.id).update(status=lifecycle, container_id="newer-container", updated_at=timezone.now())
                    return ProviderRuntimeStartResult("late-container", "http://late/login", "http://late/v1", health=ProviderRuntimeHealthResult(True, False, "ready"))

                runner.restore.side_effect = restore
                self.assertEqual(reconcile_stale_provider_runtime_starts()["recovered"], 0)
                runtime.refresh_from_db()
                self.assertEqual(runtime.status, lifecycle)
                self.assertEqual(runtime.container_id, "newer-container")
                if lifecycle in {"stopped", SoftDeleteModel.STATUS_DELETED}:
                    runner.stop_restored_container.assert_called_once()
                    self.assertEqual(runner.stop_restored_container.call_args.kwargs["container_id"], "late-container")
                    self.assertEqual(runner.stop_restored_container.call_args.kwargs["runtime"].status, lifecycle)
                else:
                    runner.stop_restored_container.assert_not_called()
                # Do not let a deliberately new STARTING generation be selected
                # as another stale entry in the next subcase.
                ProviderRuntimeAccount.objects.filter(id=runtime.id).update(status="stopped")

    @override_settings(NEXUS_PROVIDER_LIFECYCLE_STALE_SECONDS=1, NEXUS_PROVIDER_RUNTIME_RUNNER="fake")
    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_stale_start_approval_rejection_is_sanitized_and_not_retryable(self, get_runner):
        from rest_framework.exceptions import APIException

        runtime = ProviderAccount.objects.get(id=self.create_connection(engine="codex_proxy")["id"]).source_runtime_accounts.get()
        for failure in (APIException("Provider release approval is missing or invalid; install a verified release receipt."), RuntimeError("Bearer private-detail")):
            with self.subTest(category=type(failure).__name__):
                ProviderRuntimeAccount.objects.filter(id=runtime.id).update(status="starting", encrypted_proxy_api_key=encrypt_secret("recovery-key"), updated_at=timezone.now() - timedelta(seconds=5))
                get_runner.return_value.restore.side_effect = failure
                self.assertEqual(reconcile_stale_provider_runtime_starts()["failed"], 1)
                runtime.refresh_from_db()
                self.assertNotIn("private-detail", runtime.last_error)
                self.assertFalse(runtime.last_error.startswith("Automatic start recovery failed:"))

    @override_settings(NEXUS_PROVIDER_LIFECYCLE_STALE_SECONDS=1, NEXUS_PROVIDER_RUNTIME_RUNNER="fake")
    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_stale_stop_failure_does_not_persist_private_exception(self, get_runner):
        runtime = ProviderAccount.objects.get(id=self.create_connection(engine="codex_proxy")["id"]).source_runtime_accounts.get()
        ProviderRuntimeAccount.objects.filter(id=runtime.id).update(status="stopping", updated_at=timezone.now() - timedelta(seconds=5))
        get_runner.return_value.stop.side_effect = RuntimeError("Bearer private-detail")
        self.assertEqual(reconcile_stale_provider_runtime_stops()["failed"], 1)
        runtime.refresh_from_db()
        self.assertEqual(runtime.status, "stopping")
        self.assertNotIn("private-detail", runtime.last_error)

    @override_settings(NEXUS_PROVIDER_LIFECYCLE_STALE_SECONDS=1, NEXUS_PROVIDER_RUNTIME_RUNNER="fake")
    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_interrupted_stop_is_recovered_without_user_retry(self, get_runner):
        created = self.create_connection(engine="codex_proxy", upstream="openai")
        runtime = ProviderAccount.objects.get(id=created["id"]).source_runtime_accounts.get()
        ProviderRuntimeAccount.objects.filter(id=runtime.id).update(
            status=ProviderRuntimeAccount.STATUS_STOPPING,
            updated_at=timezone.now() - timedelta(seconds=5),
        )

        result = reconcile_stale_provider_runtime_stops()

        self.assertEqual(result, {"examined": 1, "stopped": 1, "failed": 0})
        runtime.refresh_from_db()
        self.assertEqual(runtime.status, ProviderRuntimeAccount.STATUS_STOPPED)
        get_runner.return_value.stop.assert_called_once()
