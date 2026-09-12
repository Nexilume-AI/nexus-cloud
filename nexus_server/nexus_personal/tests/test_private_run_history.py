"""History using real Personal sessions and owner scope, without Enterprise fixtures."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from apps.accounts.models import AccountProfile
from apps.agents.models import AgentDisplayRun
from tests.private_run_history_guards import PrivateRunHistoryGuards
from . import test_private_run_flow as fixtures


class PersonalPrivateRunHistoryTests(PrivateRunHistoryGuards, TestCase):
    _headers = fixtures.PersonalPrivateRunFlowTests._headers
    _display_headers = fixtures.PersonalPrivateRunFlowTests._display_headers
    _create = fixtures.PersonalPrivateRunFlowTests._create
    private_payload = fixtures.PersonalPrivateRunFlowTests.private_payload
    history_payload = fixtures.PersonalPrivateRunFlowTests.private_payload
    authenticate_private_caller = fixtures.PersonalPrivateRunFlowTests.authenticate_private_caller
    create_private_runtime = fixtures.PersonalPrivateRunFlowTests.create_private_runtime

    def setUp(self):
        fixtures.PersonalPrivateRunFlowTests.setUp(self)
        self.path = f"/api/v1/agents/{self.agent.pk}/private-runs/"
        response = self._create("first")
        self.assertEqual(response.status_code, 202, response.content)
        self.run = AgentDisplayRun.objects.get(pk=self.private_payload(response)["run_id"])
        self.headers = self._display_headers(str(self.run.pk))
        self.read_url = f"/api/v1/agent-runs/{self.run.pk}/read/"

    def test_receipt_requires_real_owner_and_csrf(self):
        self.complete()
        body = {"completed_at": self.run.completed_at.isoformat()}
        display = {"HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN": self.headers["HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN"]}
        self.assertEqual(self.client.post(self.read_url, body, format="json", **display).status_code, 403)
        self.assertEqual(APIClient().post(self.read_url, body, format="json", **self.headers).status_code, 401)
        other = get_user_model().objects.create_user(username="foreign-history-reader")
        AccountProfile.objects.create(user=other, tenant_id=str(self.tenant.pk),
            project_id=str(self.project_a.pk), status="active")
        peer = APIClient()
        peer.credentials(HTTP_AUTHORIZATION="Bearer " + Token.objects.create(user=other).key)
        self.assertEqual(peer.post(self.read_url, body, format="json", **self.headers).status_code, 401)
        self.assertEqual(peer.get(self.path).status_code, 401)
        self.run.refresh_from_db()
        self.assertIsNone(self.run.caller_read_completed_at)

    def test_forged_token_foreign_project_and_hidden_run_cannot_mark_read(self):
        self.complete()
        original = dict(self.headers)
        self.headers["HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN"] = "forged"
        self.assertEqual(self.mark().status_code, 404)
        self.headers = {**original, "HTTP_X_NEXUS_PROJECT": "foreign-project"}
        self.assertEqual(self.mark().status_code, 403)
        self.headers = original
        self.run.caller_hidden_at = timezone.now()
        self.run.save(update_fields=["caller_hidden_at"])
        self.assertEqual(self.mark().status_code, 404)
        self.assertEqual(self.history_payload(self.listing())["counts"]["all"], 0)
        self.run.refresh_from_db()
        self.assertIsNone(self.run.caller_read_completed_at)
