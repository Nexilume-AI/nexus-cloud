"""Shared image lifecycle over real owner HTTP/auth/storage; no Agent is executed."""
import tempfile
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from apps.accounts.models import AccountProfile
from apps.agents.models import Agent, AgentVersion, AgentRuntimeImage, AgentRuntimeDeployment, AgentDisplayRun
from nexus_personal.services import provision_owner
from tests.agent_image_http_guards import AgentImageHTTPGuards
from .test_installation import PASSWORD


class PersonalAgentImageTests(AgentImageHTTPGuards, TestCase):
    def setUp(self):
        row = provision_owner(email="agent-images@example.test", password=PASSWORD)
        self.row, self.tenant, self.project_a = row, row.tenant, row.project
        self.assertFalse(row.owner.is_superuser)
        self.client = APIClient(enforce_csrf_checks=True)
        self.assertTrue(self.client.login(username=row.owner.username, password=PASSWORD))
        self.client.get("/api/v1/public/bootstrap/")
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        config = override_settings(MEDIA_ROOT=folder.name, NEXUS_MEDIA_STORAGE_ROOT=folder.name,
            NEXUS_DATASET_STORAGE_ROOT=folder.name, NEXUS_DATASET_STORAGE_BACKEND="local")
        config.enable()
        self.addCleanup(config.disable)
        self.agent = Agent.objects.create(tenant=row.tenant, project=row.project,
            name="Personal images", created_by=row.owner, current_version="v1", status="active")
        version = AgentVersion.objects.create(agent=self.agent, version="v1", created_by=row.owner,
            tool_runtime_policy={"inspect": {"task": True, "interactive": True, "continuable": True,
                "recovery_protocol": 1, "chat": True}})
        image = AgentRuntimeImage.objects.create(tenant=row.tenant, project=row.project,
            agent=self.agent, version=version, image_ref="fixture.invalid/agent-images:v1", created_by=row.owner)
        AgentRuntimeDeployment.objects.create(tenant=row.tenant, project=row.project, agent=self.agent,
            image=image, status="active", health_status="healthy",
            container_id="metadata-only-no-container", internal_mcp_url="http://fixture.invalid/mcp")
        self.agent.repo_metadata = {"tools": [{"name": "inspect", "input_schema": {
            "type": "object", "properties": {"message": {"type": "string"}, "attachments": {"type": "array", "items": {"type": "object",
                "properties": {"asset_id": {"type": "string"}, "content_type": {"type": "string"}}, "required": ["asset_id"]}}}, "required": ["message"]}}]}
        self.agent.save()

    def _headers(self, project=None):
        if project is not None:
            self.assertEqual(project.pk, self.row.project_id)
        return {"HTTP_X_CSRFTOKEN": self.client.cookies["csrftoken"].value}

    def image_payload(self, response):
        return response.json()

    def _display_headers(self, run_id):
        response = self.client.post(f"/api/v1/agent-runs/{run_id}/display-token/", {},
            format="json", **self._headers())
        self.assertEqual(response.status_code, 200, response.content)
        return {**self._headers(), "HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN": response.json()["display_token"]}

    def assert_other_caller_cannot_read(self, run, asset, display_headers):
        other = get_user_model().objects.create_user(username="other-image-caller")
        AccountProfile.objects.create(user=other, tenant_id=str(self.tenant.pk),
            project_id=str(self.project_a.pk), status="active")
        token = Token.objects.create(user=other)
        peer = APIClient()
        peer.credentials(HTTP_AUTHORIZATION="Bearer " + token.key)
        response = peer.get(f"/api/v1/agent-runs/{run.pk}/display-assets/{asset.pk}/", **display_headers)
        # A non-owner token is rejected by Personal authentication itself;
        # unlike the Enterprise case, it never becomes an authorized caller.
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("image/png", response.get("Content-Type", ""))

    def test_upload_and_run_start_require_owner_authentication_and_csrf(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from tests.image_media_guards import png
        response = self.client.post("/api/v1/media/assets/",
            {"file": SimpleUploadedFile("input.png", png(), content_type="image/png"), "purpose": "chat_input"})
        self.assertEqual(response.status_code, 403)
        asset_id = self.upload()
        path = f"/api/v1/agents/{self.agent.pk}/private-runs/"
        body = {"content": "Read image", "attachments": [{"asset_id": asset_id}]}
        self.assertEqual(self.client.post(path, body, format="json").status_code, 403)
        self.assertEqual(APIClient().post(path, body, format="json").status_code, 401)
        self.assertFalse(AgentDisplayRun.objects.exists())
