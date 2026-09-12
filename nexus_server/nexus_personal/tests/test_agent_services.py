"""Real shared Agent services, not a complete standalone Agent API host."""
from decimal import Decimal
from types import SimpleNamespace
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework import exceptions
from apps.agents import services
from apps.agents.models import Agent, AgentResourceConfig, AgentDisplayRun
from apps.audit.models import AuditLog
from apps.common.authorization import has_nexus_permission
from apps.common.resource_limits import capability_state
from apps.common.subjects import request_subject
from apps.tenancy.models import Tenant, Project
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


class PersonalAgentServicesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="agent-owner@example.test", password=PASSWORD)
        cls.other = get_user_model().objects.create_user(username="other")

    def request(self):
        return SimpleNamespace(user=self.row.owner, META={}, query_params={}, headers={},
            tenant_id=str(self.row.tenant_id), project_id=str(self.row.project_id))

    def create(self, name="Assistant"):
        return services.create_agent(request=self.request(), name=name)

    def test_real_create_clone_metadata_and_resources_without_pricing_rows(self):
        agent = self.create()
        self.assertEqual(agent.project_id, self.row.project_id)
        self.assertEqual(agent.created_by_id, self.row.owner_id)
        agent.repo_metadata = {"tools": [{"name": "echo"}]}
        agent.save()
        AgentResourceConfig.objects.create(agent=agent, cpu="1", memory="512M")
        clone = services.clone_agent(request=self.request(), agent_id=str(agent.pk))
        self.assertNotEqual(agent.pk, clone.pk)
        self.assertEqual(clone.repo_metadata, agent.repo_metadata)
        self.assertEqual(clone.resource_config.memory, "512M")
        self.assertEqual(clone.publication_status, "unpublished")
        self.assertEqual(capability_state(tenant=self.row.tenant, code="agents.agents")["used"], Decimal(2))
        self.assertTrue(AuditLog.objects.filter(action="agents.clone", resource_id=str(clone.pk)).exists())

    def test_count_limit_prevents_create_and_clone_without_orphans(self):
        with override_settings(NEXUS_PERSONAL_AGENT_LIMITS={"agents.agents": 1}):
            agent = self.create()
            for operation in (lambda: self.create("Excess"),
                lambda: services.clone_agent(request=self.request(), agent_id=str(agent.pk))):
                with self.assertRaises(exceptions.APIException) as error:
                    operation()
                self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(Agent.objects.count(), 1)
            self.assertFalse(AuditLog.objects.filter(action="agents.clone").exists())
        agent.status = "deleted"
        agent.save()
        with override_settings(NEXUS_PERSONAL_AGENT_LIMITS={"agents.agents": 1}):
            self.create("Replacement")

    def test_list_queries_real_relations_without_pricing_and_hides_foreign_rows(self):
        own = self.create()
        other_tenant = Tenant.objects.create(name="Other", slug="other")
        other_project = Project.objects.create(tenant=self.row.tenant, name="Other")
        bad = [
            Agent.objects.create(tenant=other_tenant, name="OtherTenant", created_by=self.row.owner),
            Agent.objects.create(tenant=self.row.tenant, project=other_project, name="OtherProject", created_by=self.row.owner),
            Agent.objects.create(tenant=self.row.tenant, name="OtherOwner", created_by=self.other),
        ]
        self.assertEqual([row.pk for row in services.list_agents(request=self.request())], [own.pk])
        for agent in bad:
            with self.assertRaises(services.AgentNotFound):
                services.get_agent(request=self.request(), agent_id=str(agent.pk))

    def test_actual_rename_and_memory_records_are_preserved(self):
        agent = self.create()
        renamed = services.update_agent(request=self.request(), agent_id=str(agent.pk), data={"name": "Renamed"})
        self.assertEqual(renamed.name, "Renamed")
        with self.assertRaises(exceptions.ValidationError):
            services.update_agent(request=self.request(), agent_id=str(agent.pk), data={"name": "Invalid name!"})
        agent.refresh_from_db()
        self.assertEqual(agent.name, "Renamed")
        # The production serializer supplies the explicit confidence default.
        memory = services.create_memory_item(request=self.request(), agent_id=str(agent.pk),
            data={"content_text": "Remember", "confidence": Decimal("1")})
        self.assertEqual(list(services.list_memory_items(request=self.request(), agent_id=str(agent.pk))), [memory])

    def test_owner_authorization_is_not_an_unknown_action_or_resource_bypass(self):
        agent = self.create()
        for action in ("admin", "use", "agent.observability.read"):
            self.assertTrue(has_nexus_permission(self.row.owner, self.row.tenant, action, "agent", str(agent.pk)))
        for action, resource, identity in (("admin", "wallet", str(agent.pk)),
            ("billing.admin", None, None), ("use", None, None), ("admin", "agent", "invalid")):
            self.assertFalse(has_nexus_permission(self.row.owner, self.row.tenant, action, resource, identity))
        self.assertFalse(has_nexus_permission(self.other, self.row.tenant, "admin"))
        Agent.objects.filter(pk=agent.pk).update(created_by=self.other)
        self.assertFalse(has_nexus_permission(self.row.owner, self.row.tenant, "admin", "agent", str(agent.pk)))

    def test_no_marketplace_or_financial_operation_is_faked(self):
        agent = self.create()
        for operation in (
            lambda: services.set_pricing(request=self.request(), agent_id=str(agent.pk), pricing_type="fixed", price=Decimal(1)),
            lambda: services.set_publication(request=self.request(), agent_id=str(agent.pk), action="publish"),
            lambda: services.list_marketplace_agents(request=self.request()),
            lambda: services.set_visibility(request=self.request(), agent_id=str(agent.pk), visibility="public"),
        ):
            with self.assertRaises(exceptions.NotFound):
                operation()
        agent.refresh_from_db()
        self.assertEqual(agent.publication_status, "unpublished")
        self.assertEqual(agent.visibility, "private")

    def test_real_private_display_token_is_owner_run_and_context_bound(self):
        agent = self.create()
        request = self.request()
        subject = request_subject(request)
        display_run = AgentDisplayRun.objects.create(tenant=self.row.tenant, agent=agent,
            run_kind="invocation", consumer_tenant=self.row.tenant, consumer_project=self.row.project,
            caller_principal_type="user", caller_principal_id=str(self.row.owner_id),
            caller_subject_hash=subject.subject_hash, write_token="fixture-not-live-token")
        _, token = services.issue_private_display_token(request=request, run_id=str(display_run.pk))
        request.headers = {"X-Nexus-Agent-Display-Token": token}
        self.assertEqual(services.get_private_display_run(request=request, run_id=str(display_run.pk)).pk, display_run.pk)
        request.headers = {"X-Nexus-Agent-Display-Token": token + "tampered"}
        with self.assertRaises(services.AgentNotFound):
            services.get_private_display_run(request=request, run_id=str(display_run.pk))
        request.headers = {"X-Nexus-Agent-Display-Token": token}
        request.user = self.other
        with self.assertRaises(exceptions.APIException):
            services.get_private_display_run(request=request, run_id=str(display_run.pk))

    def test_legacy_demo_entry_points_fail_before_any_session_or_runtime_dispatch(self):
        from unittest.mock import patch
        from apps.agents import runtime_services
        agent = self.create()
        request = self.request()
        request.nexus_demo_session_token = "fixture-demo-not-a-live-token"
        entries = (
            lambda: services.public_demo_cookie_name(str(agent.pk)),
            lambda: services.get_public_display_run(agent_id=str(agent.pk), request=request),
            lambda: services.get_public_display_asset(request=request, agent_id=str(agent.pk),
                run_id=str(agent.pk), asset_id=str(agent.pk)),
            lambda: services.submit_public_display_message(request=request, agent_id=str(agent.pk),
                run_id=None, content="answer"),
            lambda: runtime_services.start_public_agent_demo(request=request, agent_id=str(agent.pk)),
            lambda: runtime_services._execute_public_demo(runtime_id=str(agent.pk),
                run_id=str(agent.pk), context=None, body=b"{}"),
        )
        with patch.object(runtime_services.threading, "Thread") as thread:
            for index, operation in enumerate(entries):
                with self.subTest(entry=index), self.assertNumQueries(0), self.assertRaises(exceptions.NotFound):
                    operation()
            thread.assert_not_called()
        self.assertFalse(AgentDisplayRun.objects.exists())

    def test_disabled_owner_cannot_use_stale_user_or_create_an_agent(self):
        get_user_model().objects.filter(pk=self.row.owner_id).update(is_active=False)
        self.assertFalse(has_nexus_permission(self.row.owner, self.row.tenant, "admin"))
        with self.assertRaises(exceptions.APIException):
            self.create()
        self.assertFalse(Agent.objects.exists())

    def test_personal_update_cannot_transfer_into_other_or_organization_scope(self):
        agent = self.create()
        other_project = Project.objects.create(tenant=self.row.tenant, name="Other")
        for data in ({"project_id": str(other_project.pk)}, {"project_id": None}, {"team_id": None}):
            with self.assertRaises(exceptions.ValidationError):
                services.update_agent(request=self.request(), agent_id=str(agent.pk), data={"name": "MustNotPersist", **data})
            agent.refresh_from_db()
            self.assertEqual(agent.name, "Assistant")
            self.assertEqual(agent.project_id, self.row.project_id)

    def test_public_agent_in_another_tenant_is_not_a_personal_bind_target(self):
        tenant = Tenant.objects.create(name="Other", slug="other")
        other = Agent.objects.create(tenant=tenant, name="Published", visibility="public", publication_status="published")
        with self.assertRaises(services.AgentNotFound):
            services._get_bindable_agent(request=self.request(), agent_id=str(other.pk))
        own = self.create()
        self.assertEqual(services._get_bindable_agent(request=self.request(), agent_id=str(own.pk)).pk, own.pk)
