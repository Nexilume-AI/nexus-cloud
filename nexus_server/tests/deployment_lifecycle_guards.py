"""Shared Source immutability, soft deletion and persistent health assertions."""
from apps.audit.models import AuditLog
from apps.common.models import SoftDeleteModel
from apps.deployments.models import Deployment, DeploymentHealthCheck, ModelGroupDeployment
from apps.deployments.tasks import check_all_active_deployments


class DeploymentLifecycleGuards:
    def test_update_endpoint_is_rejected_for_immutable_source(self) -> None:
        deployment = self.deployment_fixture()
        self.authorize_routing()

        response = self.routing_request("patch",
            self.source_path(deployment),
            {"endpoint": "https://proxy.example/v1"},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["error"]["code"], "SOURCE_IMMUTABLE")
        deployment.refresh_from_db()
        self.assertNotEqual(deployment.endpoint, "https://proxy.example/v1")
        self.assertFalse(AuditLog.objects.filter(action="deployments.update", resource_id=str(deployment.id)).exists())

    def test_disable_deployment(self) -> None:
        deployment = self.deployment_fixture()
        self.authorize_routing()

        response = self.routing_request("post",
            self.source_path(deployment) + "disable/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.routing_payload(response)["status"], SoftDeleteModel.STATUS_DISABLED)
        deployment.refresh_from_db()
        self.assertEqual(deployment.status, SoftDeleteModel.STATUS_DISABLED)
        log = AuditLog.objects.get(action="deployments.disable", resource_id=str(deployment.id))
        self.assertEqual(log.before_snapshot["status"], SoftDeleteModel.STATUS_ACTIVE)
        self.assertEqual(log.after_snapshot["status"], SoftDeleteModel.STATUS_DISABLED)

    def test_delete_deployment_preserves_empty_model_pool(self) -> None:
        deployment = self.deployment_fixture()
        self.authorize_routing()

        response = self.routing_request("delete",
            self.source_path(deployment),
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        sources_response = self.routing_request("get",
            "/api/v1/deployments/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        models_response = self.routing_request("get",
            "/api/v1/models/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 200, response.content)
        deployment.refresh_from_db()
        self.assertEqual(deployment.status, SoftDeleteModel.STATUS_DELETED)
        self.assertIsNotNone(deployment.deleted_at)
        self.assertEqual(self.routing_payload(sources_response), [])
        self.assertEqual(len(self.routing_payload(models_response)), 1)
        self.assertEqual(self.routing_payload(models_response)[0]["deployments"], [])
        self.assertFalse(ModelGroupDeployment.objects.filter(deployment=deployment).exclude(status=SoftDeleteModel.STATUS_DELETED).exists())
        self.assertTrue(AuditLog.objects.filter(action="deployments.delete", resource_id=str(deployment.id)).exists())

    def test_health_check_mock_deployment_marks_healthy(self) -> None:
        deployment = self.deployment_fixture(endpoint="http://mock.local/v1")
        self.authorize_routing()

        response = self.routing_request("post",
            self.source_path(deployment) + "health-check/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 200)
        payload = self.routing_payload(response)
        self.assertEqual(payload["health_status"], Deployment.HEALTH_HEALTHY)
        self.assertEqual(payload["consecutive_failures"], 0)
        deployment.refresh_from_db()
        self.assertEqual(deployment.health_status, Deployment.HEALTH_HEALTHY)
        self.assertIsNotNone(deployment.last_checked_at)
        self.assertTrue(DeploymentHealthCheck.objects.filter(deployment=deployment, status=Deployment.HEALTH_HEALTHY).exists())

    def test_health_check_fail_deployment_marks_unhealthy(self) -> None:
        deployment = self.deployment_fixture(endpoint="http://fail.local/v1")
        self.authorize_routing()

        response = self.routing_request("post",
            self.source_path(deployment) + "health-check/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 200)
        payload = self.routing_payload(response)
        self.assertEqual(payload["health_status"], Deployment.HEALTH_UNHEALTHY)
        self.assertEqual(payload["consecutive_failures"], 1)
        self.assertTrue(DeploymentHealthCheck.objects.filter(deployment=deployment, status=Deployment.HEALTH_UNHEALTHY).exists())
        self.assertTrue(AuditLog.objects.filter(action="deployment.health.unhealthy", resource_id=str(deployment.id)).exists())

    def test_deployment_status_includes_health_fields(self) -> None:
        deployment = self.deployment_fixture(endpoint="http://mock.local/v1")
        check_all_active_deployments()
        self.authorize_routing()

        response = self.routing_request("get",
            "/api/v1/deployments/status/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 200)
        item = self.routing_payload(response)[0]
        self.assertEqual(item["deployment_id"], deployment.deployment_id)
        self.assertEqual(item["health_status"], Deployment.HEALTH_HEALTHY)
        self.assertIn("last_checked_at", item)
