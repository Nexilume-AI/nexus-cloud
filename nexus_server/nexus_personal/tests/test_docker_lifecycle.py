"""Real owner HTTP and lifecycle state machine with test-only fake execution.

This is not production Docker, restart, egress or admission acceptance.
"""
from django.test import TestCase, override_settings
from django.core.cache import cache
from unittest.mock import patch
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from apps.agents import docker_lifecycle as lifecycle
from apps.agents.models import Agent, AgentVersion, AgentRuntimeImage, AgentRuntimeDeployment
from nexus_personal.services import provision_owner
from tests.docker_lifecycle_guards import DockerLifecycleGuards
from .test_installation import PASSWORD


@override_settings(NEXUS_AGENT_RUNTIME_RUNNER="fake", CELERY_TASK_ALWAYS_EAGER=True)
class PersonalDockerLifecycleTests(DockerLifecycleGuards, TestCase):
    def setUp(self):
        from apps.agents.tasks import deploy_runtime_job
        # The isolated test host deliberately does not load either edition's
        # production Celery app. Configure only this unit-test task application;
        # Django's CELERY_* override alone cannot enable the default app's eager mode.
        eager = patch.dict(deploy_runtime_job.app.conf.changes, task_always_eager=True,
            task_eager_propagates=False, broker_url="memory://", result_backend="cache+memory://")
        eager.start()
        self.addCleanup(eager.stop)
        self.assertTrue(deploy_runtime_job.app.conf.task_always_eager)
        self.row = provision_owner(email="lifecycle-owner@example.test", password=PASSWORD)
        self.tenant = self.row.tenant
        self.agent = Agent.objects.create(tenant=self.tenant, project=self.row.project,
            name="Lifecycle guard", created_by=self.row.owner, current_version="v1")
        version = AgentVersion.objects.create(agent=self.agent, version="v1", created_by=self.row.owner)
        self.image = AgentRuntimeImage.objects.create(tenant=self.tenant, project=self.row.project,
            agent=self.agent, version=version, created_by=self.row.owner,
            image_ref="fixture.invalid/lifecycle:v1", image_digest="sha256:" + "a" * 64)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + Token.objects.create(user=self.row.owner).key)

    def tearDown(self):
        cache.delete(lifecycle.RECONCILE_LOCK_KEY)

    def deploy(self):
        return self.client.post(f"/api/v1/agents/{self.agent.id}/runtime/deployments/",
            {"image_id": str(self.image.id), "env": "prod"}, format="json")

    def runtime(self):
        return AgentRuntimeDeployment.objects.get(agent=self.agent, env="prod")
