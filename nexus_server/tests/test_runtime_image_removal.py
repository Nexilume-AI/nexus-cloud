from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import threading
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import close_old_connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.agents.models import Agent, AgentVersion, AgentPythonBuild, AgentRuntimeImage, AgentRuntimeDeployment, AgentDisplayRun, AgentExecutionTask
from apps.agents.runtime_services import AgentRuntimeOperationConflict, AgentRuntimeNotFound, resolve_image
from apps.agents.docker_lifecycle import _claim
from apps.audit.models import AuditLog
from apps.jobs.models import Job
from apps.tenancy.models import Tenant, Membership


class ImageFixtures:
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="image-owner")
        self.member = get_user_model().objects.create_user(username="image-member")
        self.tenant = Tenant.objects.create(name="Images", slug="images")
        Membership.objects.create(user=self.owner, tenant=self.tenant, role="owner")
        Membership.objects.create(user=self.member, tenant=self.tenant, role="member")
        self.agent = Agent.objects.create(tenant=self.tenant, name="Image Agent", created_by=self.owner)
        self.version = AgentVersion.objects.create(agent=self.agent, version="v1")
        self.old = AgentRuntimeImage.objects.create(agent=self.agent, tenant=self.tenant, version=self.version, image_ref="example/agent:old")
        self.new = AgentRuntimeImage.objects.create(agent=self.agent, tenant=self.tenant, image_ref="example/agent:new")
        self.agent.current_image = self.old
        self.agent.save()
        self.headers = {"HTTP_X_NEXUS_TENANT": str(self.tenant.pk)}
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.base = f"/api/v1/agents/{self.agent.pk}/runtime/"

    def remove(self, image=None):
        return self.client.delete(self.base + f"images/{(image or self.old).pk}/", **self.headers)

    def runtime(self, image=None, **kwargs):
        return AgentRuntimeDeployment.objects.create(agent=self.agent, tenant=self.tenant, image=image or self.new,
                                                     env="prod", **kwargs)


@override_settings(NEXUS_AGENT_RUNTIME_RUNNER="fake")
class RuntimeImageRemovalTests(ImageFixtures, TestCase):
    def test_unused_default_removed_and_deployed_default_selected_without_redeploy(self):
        runtime = self.runtime(status="active", health_status="healthy")
        build = AgentPythonBuild.objects.create(agent=self.agent, image=self.old, status="succeeded", filename="agent.py", source="keep")
        with mock.patch("apps.agents.runtime_services.get_runtime_runner") as runner:
            response = self.remove()
        self.assertEqual(response.status_code, 200, response.content)
        runner.assert_not_called()
        self.assertTrue(response.data["artifacts_retained"])
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.current_image_id, self.new.pk)
        self.old.refresh_from_db()
        self.assertEqual(self.old.status, "deleted")
        self.assertIsNotNone(self.old.deleted_at)
        runtime.refresh_from_db()
        self.assertEqual(runtime.image_id, self.new.pk)
        self.assertEqual(runtime.status, "active")
        build.refresh_from_db()
        self.assertEqual(build.source, "keep")
        self.assertEqual(build.image_id, self.old.pk)
        self.assertEqual(self.client.get(self.base + "python-builds/", **self.headers).data["results"][0]["image_removed"], True)
        self.assertEqual(AuditLog.objects.filter(action="agents.runtime.image.delete").count(), 1)
        self.assertEqual(self.remove().status_code, 404)
        self.assertEqual([i["id"] for i in self.client.get(self.base + "images/", **self.headers).data], [str(self.new.pk)])

    def test_clear_default_keeps_stopped_history_and_version(self):
        runtime = self.runtime(image=self.old, status="stopped", docker_lifecycle={"desired": "stopped"})
        self.assertEqual(self.remove().status_code, 200)
        self.agent.refresh_from_db()
        self.assertIsNone(self.agent.current_image_id)
        self.assertTrue(AgentVersion.objects.filter(pk=self.version.pk).exists())
        runtime.refresh_from_db()
        self.assertEqual(runtime.image_id, self.old.pk)
        with self.assertRaises(AgentRuntimeOperationConflict):
            _claim(runtime.pk, desired="running")

    def test_usage_distinguishes_default_deployment_and_unused(self):
        self.runtime(status="active", health_status="healthy")
        for path in ("images/", "status/"):
            response = self.client.get(self.base + path, **self.headers)
            rows = response.data if path == "images/" else response.data["images"]
            items = {row["id"]: row["usage"] for row in rows}
            self.assertEqual(items[str(self.old.pk)], {"is_default": True, "deployed_environments": [], "can_delete": True, "blocking_reasons": []})
            self.assertFalse(items[str(self.new.pk)]["is_default"])
            self.assertEqual(items[str(self.new.pk)]["deployed_environments"], ["prod"])
            self.assertFalse(items[str(self.new.pk)]["can_delete"])

    def test_active_deploying_failed_recovering_and_container_references_protected(self):
        runtime = self.runtime(image=self.old, status="active")
        for state, lifecycle, container in [
            ("active", {}, ""), ("deploying", {}, ""),
            ("failed", {"desired": "running"}, ""),
            ("failed", {}, "residual"), ("stopped", {"lease_token": "in-progress"}, ""),
        ]:
            AgentRuntimeDeployment.objects.filter(pk=runtime.pk).update(status=state, docker_lifecycle=lifecycle, container_id=container)
            result = self.remove()
            self.assertEqual(result.status_code, 409, (state, result.content))
            self.assertEqual(result.data["error"]["code"], "RUNTIME_IMAGE_IN_USE")
        self.assertFalse(AuditLog.objects.filter(action="agents.runtime.image.delete").exists())

    def test_previous_and_retiring_generations_protected(self):
        runtime = self.runtime(status="deploying", docker_lifecycle={"previous": {"image_id": str(self.old.pk)}})
        self.assertEqual(self.remove().status_code, 409)
        runtime.docker_lifecycle = {"retiring": [{"container_id": "old-container"}]}
        runtime.save()
        self.assertEqual(self.remove().status_code, 409)

    def test_queued_job_and_recovery_task_protected(self):
        job = Job.objects.create(tenant=self.tenant, job_type="agents.runtime.deploy", resource_type="agent_runtime_deployment",
                                 input_json={"agent_id": str(self.agent.pk), "image_id": str(self.old.pk)})
        self.assertEqual(self.remove().status_code, 409)
        job.status = "succeeded"
        job.save()
        run = AgentDisplayRun.objects.create(agent=self.agent, tenant=self.tenant)
        task = AgentExecutionTask.objects.create(run=run, agent=self.agent, status="waiting_for_runtime",
                 request_json={"agent_version": "v1"}, expires_at=timezone.now() + timedelta(hours=1))
        self.assertEqual(self.remove().status_code, 409)
        task.status = "failed"
        task.save()
        self.assertEqual(self.remove().status_code, 200)

    def test_permissions_and_cross_agent_targets(self):
        other = Agent.objects.create(tenant=self.tenant, name="Other")
        self.assertEqual(self.client.delete(f"/api/v1/agents/{other.pk}/runtime/images/{self.old.pk}/", **self.headers).status_code, 404)
        outside = Tenant.objects.create(name="Outside", slug="outside")
        self.assertEqual(self.client.delete(self.base + f"images/{self.old.pk}/", HTTP_X_NEXUS_TENANT=str(outside.pk)).status_code, 404)
        self.client.force_authenticate(self.member)
        self.assertIn(self.remove().status_code, [403, 404])
        self.client.force_authenticate(None)
        self.assertEqual(self.remove().status_code, 401)

    def test_removed_image_cannot_be_selected_or_deployed(self):
        self.remove()
        self.assertEqual(self.client.post(self.base + f"images/{self.old.pk}/set-current/", {}, format="json", **self.headers).status_code, 404)
        with self.assertRaises(AgentRuntimeNotFound):
            resolve_image(agent=self.agent, image_id=self.old.pk)
        with mock.patch("apps.agents.runtime_services.get_runtime_runner") as runner:
            response = self.client.post(self.base + "deployments/", {"image_id": str(self.old.pk)}, format="json", **self.headers)
        self.assertEqual(response.status_code, 404, response.content)
        runner.assert_not_called()


class RuntimeImageConcurrencyTests(ImageFixtures, TransactionTestCase):
    def test_two_removals_have_one_winner(self):
        barrier = threading.Barrier(2)
        def remove(_):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(self.owner)
                barrier.wait(timeout=10)
                return client.delete(self.base + f"images/{self.old.pk}/", **self.headers).status_code
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(remove, range(2))), [200, 404])
        self.assertEqual(AuditLog.objects.filter(action="agents.runtime.image.delete").count(), 1)

    def test_runtime_row_contention_returns_conflict_without_deadlock(self):
        runtime = self.runtime(status="active")
        def remove():
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(self.owner)
                return client.delete(self.base + f"images/{self.old.pk}/", **self.headers).status_code
            finally:
                close_old_connections()
        with transaction.atomic():
            AgentRuntimeDeployment.objects.select_for_update().get(pk=runtime.pk)
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertEqual(pool.submit(remove).result(timeout=10), 409)

    def test_deploy_and_delete_cannot_leave_removed_image_deployed(self):
        barrier = threading.Barrier(2)
        def operation(action):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(self.owner)
                barrier.wait(timeout=10)
                if action == "delete":
                    return client.delete(self.base + f"images/{self.old.pk}/", **self.headers).status_code
                return client.post(self.base + "deployments/", {"image_id": str(self.old.pk), "env": "prod"}, format="json", **self.headers).status_code
            finally:
                close_old_connections()
        with mock.patch("apps.agents.runtime_services.get_runtime_runner"), mock.patch("apps.agents.docker_lifecycle.prepare_state", return_value={}), mock.patch("apps.agents.runtime_services.dispatch_runtime_job", return_value=None):
            with ThreadPoolExecutor(max_workers=2) as pool:
                removed, deployed = list(pool.map(operation, ["delete", "deploy"]))
        self.assertIn((removed, deployed), [(200, 404), (409, 201)])
        self.old.refresh_from_db()
        if self.old.status == "deleted":
            self.assertFalse(AgentRuntimeDeployment.objects.filter(image=self.old, status="deploying").exists())

    def test_default_and_delete_cannot_select_removed_image(self):
        barrier = threading.Barrier(2)
        def operation(action):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(self.owner)
                barrier.wait(timeout=10)
                url = self.base + f"images/{self.old.pk}/"
                return (client.delete(url, **self.headers) if action == "delete" else client.post(url + "set-current/", {}, format="json", **self.headers)).status_code
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            removed, selected = list(pool.map(operation, ["delete", "default"]))
        self.assertEqual(removed, 200)
        self.assertIn(selected, [200, 404])
        self.agent.refresh_from_db()
        self.assertIsNone(self.agent.current_image_id)
