"""Real catalog HTTP/DB/authentication; not a deployed invocation or device E2E."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from datetime import timedelta
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.agents.models import (Agent, AgentMemoryItem, AgentRuntimeDeployment, AgentRuntimeImage,
    AgentDisplayRun, AgentDisplayEvent, EdgeNode, EdgeAgentRegistration)
from apps.tenancy.models import Tenant, Project
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


class PersonalAgentHTTPTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="catalog-owner@example.test", password=PASSWORD)
        cls.token = Token.objects.create(user=cls.row.owner)

    def setUp(self):
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.token.key)

    def create(self, name="Assistant"):
        response = self.client.post("/api/v1/agents/", {"name": name}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def path(self, agent, suffix=""):
        return f'/api/v1/agents/{agent["id"]}/{suffix}'

    def test_create_list_read_rename_clone_memory_and_soft_delete(self):
        agent = self.create()
        self.assertEqual(agent["project_id"], str(self.row.project_id))
        self.assertEqual(agent["computer_requirement"], Agent._meta.get_field("computer_requirement").default)
        self.assertEqual(agent["workspace_capabilities"], [])
        self.assertEqual(agent["visibility"], "private")
        for field in ("pricing", "publication_status", "publication_readiness", "team_id"):
            self.assertNotIn(field, agent)
        self.assertFalse(set(agent["allowed_actions"]) & {"share", "manage_access", "transfer", "publish"})
        response = self.client.get("/api/v1/agents/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([item["id"] for item in response.data], [agent["id"]])
        self.assertEqual(self.client.get(self.path(agent)).data["id"], agent["id"])
        response = self.client.patch(self.path(agent), {"name": "Renamed"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["name"], "Renamed")
        response = self.client.post(self.path(agent, "clone/"), {"name": "Cloned"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotEqual(response.data["id"], agent["id"])
        response = self.client.post(self.path(agent, "memory/"), {"content_text": "Remember this"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        memory = AgentMemoryItem.objects.get(pk=response.data["id"])
        self.assertEqual(memory.content_text, "Remember this")
        self.assertEqual(self.client.get(self.path(agent, "memory/")).data[0]["id"], str(memory.pk))
        self.assertEqual(self.client.delete(self.path(agent)).status_code, 204)
        self.assertEqual(Agent.objects.get(pk=agent["id"]).status, "deleted")
        self.assertEqual(self.client.get(self.path(agent)).status_code, 404)

    def test_paginated_search_cursor_binding_and_json_lifecycle_filter(self):
        agents = [self.create(name) for name in ("Alpha", "Alpine", "Beta")]
        params = {"limit": 1, "q": "Al", "sort": "name", "lifecycle": "draft"}
        response = self.client.get("/api/v1/agents/", params)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["items"][0]["name"], "Alpha")
        self.assertTrue(response.data["has_more"])
        cursor = response.data["next_cursor"]
        response = self.client.get("/api/v1/agents/", {**params, "cursor": cursor})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["items"][0]["name"], "Alpine")
        self.assertFalse(response.data["has_more"])
        for changes in ({"q": "Beta"}, {"cursor": "tampered"}, {"limit": 201}, {"lifecycle": "unknown"}):
            response = self.client.get("/api/v1/agents/", {**params, "cursor": cursor, **changes})
            self.assertEqual(response.status_code, 400, response.data)

    def test_80_agent_catalog_filters_before_paging_and_preserves_image_fallback(self):
        agents = Agent.objects.bulk_create([Agent(tenant=self.row.tenant, project=self.row.project,
            name=f'Agent {n:03}', created_by=self.row.owner) for n in range(80)])
        image = AgentRuntimeImage.objects.create(tenant=self.row.tenant, project=self.row.project,
            agent=agents[0], created_by=self.row.owner, image_ref='fixture.invalid/image:latest')
        params = {'limit': 1, 'lifecycle': 'active', 'sort': 'name'}
        response = self.client.get('/api/v1/agents/', params)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([item['id'] for item in response.data['items']], [str(agents[0].pk)])
        self.assertIsNone(response.data['next_cursor'])
        self.assertFalse(response.data['has_more'])
        # The image is metadata only: no Docker start or fallback runner. A
        # deleted image must no longer make a draft Agent appear active.
        image.status = 'deleted'
        image.save(update_fields=['status'])
        response = self.client.get('/api/v1/agents/', params)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['items'], [])
        self.assertIsNone(response.data['next_cursor'])

    def test_computer_declaration_validation_and_disabled_archive_lifecycle(self):
        agent = self.create()
        response = self.client.patch(self.path(agent), {"computer_requirement": "required",
            "workspace_capabilities": ["command.execute", "files.read", "files.read"]}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["workspace_capabilities"], ["files.read", "command.execute"])
        for body in ({"name": "bad name!"}, {"workspace_capabilities": ["arbitrary.scope"]}, {"status": "active"}):
            response = self.client.patch(self.path(agent), body, format="json")
            self.assertEqual(response.status_code, 400, response.data)
        for status in ("disabled", "archived"):
            response = self.client.patch(self.path(agent), {"status": status}, format="json")
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data["status"], status)

    def test_live_or_recovering_runtime_cannot_be_deleted(self):
        agent = self.create()
        image = AgentRuntimeImage.objects.create(agent_id=agent["id"], tenant=self.row.tenant,
            project=self.row.project, image_ref="fixture.invalid/assistant:test", created_by=self.row.owner)
        runtime = AgentRuntimeDeployment.objects.create(agent_id=agent["id"], runtime_kind="docker",
            tenant=self.row.tenant, project=self.row.project, image=image, status="active")
        self.assertEqual(self.client.get(self.path(agent)).data["runtime_status"], "active")
        self.assertEqual(self.client.delete(self.path(agent)).status_code, 400)
        runtime.status = "failed"
        runtime.docker_lifecycle = {"desired": "running"}
        runtime.save()
        self.assertEqual(self.client.delete(self.path(agent)).status_code, 400)
        runtime.docker_lifecycle = {"desired": "stopped"}
        runtime.save()
        self.assertEqual(self.client.delete(self.path(agent)).status_code, 204)

    def test_owner_context_is_enforced_for_read_write_memory_and_clone(self):
        agent = self.create()
        other = get_user_model().objects.create_user(username="unrelated")
        tenant = Tenant.objects.create(name="Foreign", slug="foreign")
        project = Project.objects.create(tenant=self.row.tenant, name="Other")
        for changes in ({"tenant": tenant}, {"project": project}, {"created_by": other}):
            values = dict(tenant=self.row.tenant, project=self.row.project,
                          name="Hidden", created_by=self.row.owner)
            hidden = Agent.objects.create(**{**values, **changes})
            item = {"id": str(hidden.pk)}
            for response in (self.client.get(self.path(item)),
                self.client.patch(self.path(item), {"name": "No"}, format="json"),
                self.client.post(self.path(item, "clone/"), {}, format="json"),
                self.client.get(self.path(item, "memory/"))):
                self.assertEqual(response.status_code, 404, response.data)
        for body in ({"ownership": {"scope": "organization"}},
                     {"ownership": {"scope": "project", "project_id": str(project.pk)}}):
            response = self.client.post("/api/v1/agents/", {"name": "Denied", **body}, format="json")
            self.assertEqual(response.status_code, 400, response.data)
        for body in ({"project_id": None}, {"project_id": str(project.pk)}):
            self.assertEqual(self.client.patch(self.path(agent), body, format="json").status_code, 400)
        self.assertEqual(self.client.get(self.path(agent), HTTP_X_NEXUS_PROJECT=str(project.pk)).status_code, 403)
        self.assertEqual(self.client.get(self.path(agent), HTTP_X_NEXUS_TENANT=str(tenant.pk)).status_code, 403)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + Token.objects.create(user=other).key)
        self.assertIn(self.client.get(self.path(agent)).status_code, (401, 403))

    def test_managed_device_declarations_render_without_device_execution_imports(self):
        agent = self.create()
        node = EdgeNode.objects.create(tenant=self.row.tenant, project=self.row.project,
            registered_by=self.row.owner, router_id="fixture-router", domain_id="fixture.invalid",
            display_name="Fixture", device_token_hash="a" * 64)
        registration = EdgeAgentRegistration.objects.create(node=node, agent_id=agent["id"],
            origin="fixture.agent", route_id="fixture-route", binding_mode="managed",
            computer_requirement="required", workspace_capabilities=["browser.control"],
            mobile_requirement="required", mobile_capabilities=["mobile.tap", "mobile.observe"],
            lease_expires_at=timezone.now() + timedelta(minutes=5), last_renewed_at=timezone.now())
        response = self.client.get(self.path(agent))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["computer_declared_by_sdk"])
        self.assertEqual(response.data["mobile_sdk_requirement"], "required")
        self.assertEqual(response.data["mobile_sdk_capabilities"], ["mobile.observe", "mobile.tap"])
        self.assertTrue(response.data["mobile_can_restore_sdk"])
        response = self.client.patch(self.path(agent), {"computer_requirement": "disabled"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        registration.mobile_requirement = "disabled"
        registration.save()
        self.assertEqual(self.client.get(self.path(agent)).data["mobile_sdk_capabilities"], [])

    def test_run_presentation_preserves_events_without_financial_fields_or_write_tokens(self):
        from apps.agents.serializers import AgentDisplayRunSerializer
        agent = self.create()
        run = AgentDisplayRun.objects.create(tenant=self.row.tenant, agent_id=agent["id"],
            caller_subject_hash="b" * 64, caller_principal_type="user",
            write_token="test-only-not-a-live-credential", title="Private history")
        AgentDisplayEvent.objects.create(tenant=self.row.tenant, agent_id=agent["id"],
            run=run, seq=3, event_type="TEXT_MESSAGE_CONTENT", payload_json={"content": "Remember"})
        payload = AgentDisplayRunSerializer(run).data
        self.assertEqual(payload["title"], "Private history")
        self.assertEqual(payload["latest_seq"], 3)
        self.assertEqual(payload["caller"], "user:" + "b" * 10)
        self.assertEqual(payload["computer_status"], "unavailable")
        for name in ("billing", "write_token", "interaction_token", "workspace_delegate_token"):
            self.assertNotIn(name, payload)

    @override_settings(NEXUS_PERSONAL_AGENT_LIMITS={"agents.agents": 1})
    def test_real_count_admission_and_authentication_not_bypassed(self):
        agent = self.create()
        self.assertEqual(self.client.post("/api/v1/agents/", {"name": "Extra"}, format="json").status_code, 409)
        self.assertEqual(self.client.post(self.path(agent, "clone/"), {}, format="json").status_code, 409)
        self.assertEqual(Agent.objects.count(), 1)
        response = self.client.get("/api/v1/agents/capabilities/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["create"])
        self.client.credentials()
        self.assertIn(self.client.get("/api/v1/agents/").status_code, (401, 403))

    def test_real_session_csrf_and_no_commercial_routes(self):
        client = APIClient(enforce_csrf_checks=True)
        self.assertTrue(client.login(username=self.row.owner.username, password=PASSWORD))
        self.assertEqual(client.get("/api/v1/agents/").status_code, 200)
        response = client.post("/api/v1/agents/", {"name": "NoCSRF"}, format="json")
        self.assertEqual(response.status_code, 403, response.data)
        csrf = client.get("/owner/").data["csrf"]
        client.cookies["csrftoken"] = csrf
        response = client.post("/api/v1/agents/", {"name": "WithCSRF"}, format="json", HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(response.status_code, 201, response.data)
        for suffix in ("pricing/", "publish/", "visibility/"):
            self.assertEqual(client.get(self.path(response.data, suffix)).status_code, 404)
        self.assertEqual(client.get("/api/v1/marketplace/agents/").status_code, 404)
