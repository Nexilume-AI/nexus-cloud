"""Shared state/recovery assertions with real Personal authority, not live transport."""
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from apps.accounts.models import AccountProfile
from apps.agents.models import (
    Agent, AgentComputerBinding, AgentDisplayRun, AgentRuntimeDeployment,
    AgentRuntimeImage, AgentVersion,
)
from apps.workspaces.models import WorkspaceConnection
from nexus_personal.services import provision_owner
from tests.private_run_flow_guards import PrivateRunFlowGuards
from .test_installation import PASSWORD


class PersonalPrivateRunFlowTests(PrivateRunFlowGuards, TransactionTestCase):
    def setUp(self):
        self.row = provision_owner(email="private-run-flow@example.test", password=PASSWORD)
        self.caller_a, self.tenant, self.project_a = self.row.owner, self.row.tenant, self.row.project
        self.assertFalse(self.caller_a.is_superuser)
        self.project_a.instructions_markdown = "Keep this task inside the selected Project."
        self.project_a.instructions_revision = 1
        self.project_a.save(update_fields=["instructions_markdown", "instructions_revision", "updated_at"])
        self.agent = Agent.objects.create(tenant=self.tenant, project=self.project_a,
            name="Run Agent", created_by=self.caller_a, current_version="v1", status="active",
            repo_metadata={"tools": [{"name": "inspect", "input_schema": {
                "type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}}]})
        self.version = AgentVersion.objects.create(agent=self.agent, version="v1", created_by=self.caller_a,
            tool_runtime_policy={"inspect": {"task": True, "interactive": True,
                "continuable": True, "recovery_protocol": 1, "chat": True}})
        image = AgentRuntimeImage.objects.create(tenant=self.tenant, project=self.project_a,
            agent=self.agent, version=self.version, image_ref="fixture.invalid/private-run:v1",
            created_by=self.caller_a)
        self.create_private_runtime(tenant=self.tenant, agent=self.agent, image=image,
            runtime_kind="docker", status="active", health_status="healthy",
            container_id="metadata-only-not-executed", internal_mcp_url="http://fixture.invalid/mcp")
        self.client = APIClient(enforce_csrf_checks=True)
        self.authenticate_private_caller()

    def authenticate_private_caller(self):
        self.assertTrue(self.client.login(username=self.caller_a.username, password=PASSWORD))
        self.client.get("/api/v1/public/bootstrap/")

    def _headers(self, project=None):
        if project is not None:
            self.assertEqual(project.pk, self.row.project_id)
        return {"HTTP_X_CSRFTOKEN": self.client.cookies["csrftoken"].value}

    def private_payload(self, response):
        return response.json()

    def create_private_connection(self, **fields):
        return WorkspaceConnection.objects.create(project=self.project_a, created_by=self.caller_a, **fields)

    def create_private_binding(self, **fields):
        return AgentComputerBinding.objects.create(project=self.project_a, **fields)

    def create_private_runtime(self, **fields):
        return AgentRuntimeDeployment.objects.create(project=self.project_a, **fields)

    def test_private_run_write_requires_owner_and_csrf(self):
        path = f"/api/v1/agents/{self.agent.pk}/private-runs/"
        self.assertEqual(self.client.post(path, {"content": "no csrf"}, format="json").status_code, 403)
        self.assertEqual(APIClient().post(path, {"content": "no owner"}, format="json").status_code, 401)
        self.assertFalse(AgentDisplayRun.objects.exists())

    def test_valid_display_token_never_overrides_owner_or_project_scope(self):
        response = self._create("private report")
        self.assertEqual(response.status_code, 202, response.content)
        run_id = self.private_payload(response)["run_id"]
        headers = self._display_headers(run_id)
        path = f"/api/v1/agent-runs/{run_id}/display/"
        denied = self.client.patch(path, {"title": "wrong project"}, format="json",
            **{**headers, "HTTP_X_NEXUS_PROJECT": "foreign-project"})
        self.assertEqual(denied.status_code, 403)
        other = get_user_model().objects.create_user(username="foreign-run-caller")
        AccountProfile.objects.create(user=other, tenant_id=str(self.tenant.pk),
            project_id=str(self.project_a.pk), status="active")
        token = Token.objects.create(user=other)
        peer = APIClient()
        peer.credentials(HTTP_AUTHORIZATION="Bearer " + token.key)
        denied = peer.patch(path, {"title": "wrong caller"}, format="json", **headers)
        self.assertEqual(denied.status_code, 401)
        run = AgentDisplayRun.objects.get(pk=run_id)
        self.assertNotIn(run.display_title, {"wrong project", "wrong caller"})
