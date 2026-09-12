"""Real owner/session Mobile HTTP flows, not a physical phone E2E claim."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts.models import AccountProfile
from apps.agents.models import Agent, AgentDisplayRun, AgentMobileBinding, AgentMobileGrant
from nexus_personal.services import provision_owner
from tests.mobile_flow_guards import MobileFlowGuards
from .test_installation import PASSWORD


class PersonalMobileFlowTests(MobileFlowGuards, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="mobile-flow@example.test", password=PASSWORD)

    def setUp(self):
        self.owner, self.tenant = self.row.owner, self.row.tenant
        self.assertFalse(self.owner.is_superuser)
        self.client = APIClient(enforce_csrf_checks=True)
        self.assertTrue(self.client.login(username=self.owner.username, password=PASSWORD))
        self.client.get("/api/v1/public/bootstrap/")
        self.client.credentials(HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value)

    def mobile_flow_payload(self, response):
        return response.json()

    def create_mobile_flow_agent(self, **fields):
        return Agent.objects.create(project=self.row.project, **fields)

    def mobile_invocation_project(self):
        return str(self.row.project_id)

    def create_mobile_flow_run(self, **fields):
        return AgentDisplayRun.objects.create(consumer_project=self.row.project,
            caller_principal_id=str(self.owner.pk), **fields)

    def assert_legacy_mobile_binding_unavailable(self, response):
        # The legacy global-binding route is not mounted by the Personal host.
        self.assertEqual(response.status_code, 404, response.content)
        self.assertFalse(AgentMobileBinding.objects.exists())

    def assert_foreign_mobile_binding_denied(self, agent, binding_id):
        other = get_user_model().objects.create_user(username="mobile-flow-outsider")
        AccountProfile.objects.create(user=other, tenant_id=str(self.tenant.pk),
            project_id=str(self.row.project_id), status="active")
        token = Token.objects.create(user=other)
        outsider = APIClient()
        outsider.credentials(HTTP_AUTHORIZATION="Bearer " + token.key)
        path = f"/api/v1/agents/{agent.pk}/mobile-bindings/"
        response = outsider.get(path)
        self.assertEqual(response.status_code, 401)
        self.assertNotIn(str(binding_id), response.content.decode())
        self.assertEqual(outsider.delete(path + str(binding_id) + "/").status_code, 401)
        self.assertTrue(AgentMobileBinding.objects.filter(pk=binding_id, status="active").exists())

    def test_mobile_grant_requires_owner_session_and_csrf(self):
        agent = Agent.objects.create(tenant=self.tenant, project=self.row.project,
            name="Mobile grant", created_by=self.owner, status="active",
            mobile_requirement="required", mobile_capabilities=["mobile.observe"])
        path = f"/api/v1/agents/{agent.pk}/mobile-grant/"
        payload = {"scopes": ["mobile.observe"]}
        self.client.credentials()
        self.assertEqual(self.client.put(path, payload, format="json").status_code, 403)
        self.assertEqual(APIClient().put(path, payload, format="json").status_code, 401)
        self.assertFalse(AgentMobileGrant.objects.filter(agent=agent).exists())
