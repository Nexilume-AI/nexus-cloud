"""Original lifecycle guards plus real HTTP unhealthy-to-healthy recovery."""
from django.test import TestCase, override_settings
from uuid import uuid4
from apps.audit.models import AuditLog
from apps.deployments.models import DeploymentHealthCheck
from tests.deployment_lifecycle_guards import DeploymentLifecycleGuards
from .deployment_guard_fixture import PersonalDeploymentGuardFixture


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1")
class PersonalDeploymentLifecycleTests(DeploymentLifecycleGuards, PersonalDeploymentGuardFixture, TestCase):
    def test_source_identity_edits_cannot_mutate_credential_model_or_endpoint(self):
        deployment = self.deployment_fixture()
        fields = ("provider_id", "provider_account_id", "provider_runtime_id",
                  "canonical_model_id", "upstream_model_id", "endpoint")
        original = tuple(getattr(deployment, field) for field in fields)
        response = self.patch(self.source_path(deployment), {
            "provider": "qwen", "canonical_model_id": str(uuid4()),
            "upstream_model_id": "qwen-max", "endpoint": "https://other.example/v1"})
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.json()["error"]["code"], "SOURCE_IMMUTABLE")
        deployment.refresh_from_db()
        self.assertEqual(tuple(getattr(deployment, field) for field in fields), original)
        self.assertFalse(AuditLog.objects.filter(action="deployments.update",
                                                 resource_id=str(deployment.pk)).exists())

    def test_removing_source_keeps_real_runtime_offer_and_credential_usable(self):
        deployment = self.deployment_fixture()
        runtime = deployment.provider_runtime
        account = deployment.provider_account
        credential = account.encrypted_key
        offer_id = deployment.runtime_model_offer_id
        response = self.client.delete(self.source_path(deployment), **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        deployment.refresh_from_db()
        runtime.refresh_from_db()
        account.refresh_from_db()
        self.assertEqual(deployment.status, "deleted")
        self.assertEqual(runtime.status, "active")
        self.assertEqual(account.encrypted_key, credential)
        self.assertTrue(runtime.model_offers.filter(pk=offer_id).exists())
        self.assertFalse(runtime.sources.exclude(status="deleted").exists())
        # The remaining credential is exercised by a real HTTP model refresh.
        path = f"/api/v1/provider-connections/{account.pk}/models/refresh/"
        refreshed = self.post(path)
        self.assertEqual(refreshed.status_code, 200, refreshed.data)
        self.assertEqual(refreshed.data["models"][0]["id"], str(offer_id))
        self.assertEqual(refreshed.data["models"][0]["health_status"], "healthy")
        self.assert_safe(refreshed.data)

    def test_real_health_failure_and_recovery_preserve_history_and_reset_counter(self):
        deployment = self.deployment_fixture()
        path = self.source_path(deployment) + "health-check/"
        before = DeploymentHealthCheck.objects.filter(deployment=deployment).count()
        type(self).catalog_status = 503
        for expected in (1, 2):
            failed = self.post(path)
            self.assertEqual(failed.status_code, 200, failed.data)
            self.assertEqual(failed.data["health_status"], "unhealthy")
            self.assertEqual(failed.data["consecutive_failures"], expected)
        type(self).catalog_status = 200
        recovered = self.post(path)
        self.assertEqual(recovered.status_code, 200, recovered.data)
        self.assertEqual(recovered.data["health_status"], "healthy")
        self.assertEqual(recovered.data["consecutive_failures"], 0)
        self.assertEqual(self.calls, [("GET", "/v1/models")] * 3)
        deployment.refresh_from_db()
        self.assertIsNotNone(deployment.last_failure_at)
        self.assertGreaterEqual(deployment.last_success_at, deployment.last_failure_at)
        history = DeploymentHealthCheck.objects.filter(deployment=deployment)
        self.assertEqual(history.count(), before + 3)
        self.assertEqual(history.filter(status="unhealthy").count(), 2)
        for status in ("healthy", "unhealthy"):
            self.assertTrue(AuditLog.objects.filter(action="deployment.health." + status,
                                                    resource_id=str(deployment.pk)).exists())
        self.assert_safe(recovered.data)
