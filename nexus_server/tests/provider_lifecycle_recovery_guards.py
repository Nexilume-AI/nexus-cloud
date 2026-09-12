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
from apps.providers.tasks import refresh_active_provider_runtime_models


class ProviderLifecycleRecoveryGuards:
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

    @override_settings(NEXUS_PROVIDER_LIFECYCLE_STALE_SECONDS=1)
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

    @override_settings(NEXUS_PROVIDER_LIFECYCLE_STALE_SECONDS=1)
    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_failed_start_recovery_becomes_retryable(self, get_runner):
        created = self.create_connection(engine="codex_proxy", upstream="openai")
        runtime = ProviderAccount.objects.get(id=created["id"]).source_runtime_accounts.get()
        ProviderRuntimeAccount.objects.filter(id=runtime.id).update(
            status=ProviderRuntimeAccount.STATUS_STARTING,
            updated_at=timezone.now() - timedelta(seconds=5),
        )
        get_runner.return_value.restore.side_effect = RuntimeError("docker unavailable")

        result = reconcile_stale_provider_runtime_starts()

        self.assertEqual(result, {"examined": 1, "recovered": 0, "failed": 1})
        runtime.refresh_from_db()
        self.assertEqual(runtime.status, ProviderRuntimeAccount.STATUS_FAILED)
        self.assertIn("Automatic start recovery failed", runtime.last_error)

    @override_settings(NEXUS_PROVIDER_LIFECYCLE_STALE_SECONDS=1)
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
