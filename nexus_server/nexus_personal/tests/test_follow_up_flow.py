"""Personal queue/steer flow through owner sessions and real durable Tasks."""
import json
from unittest import mock
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from apps.accounts.models import AccountProfile
from apps.agents.models import AgentExecutionTask, AgentRunFollowUp
from apps.common.crypto import decrypt_secret
from tests.follow_up_guards import FollowUpHelpers, FollowUpStateGuards
from . import test_private_run_flow as fixtures


class PersonalFollowUpFixture(FollowUpHelpers):
    authenticate_private_caller = fixtures.PersonalPrivateRunFlowTests.authenticate_private_caller
    create_private_runtime = fixtures.PersonalPrivateRunFlowTests.create_private_runtime
    private_payload = fixtures.PersonalPrivateRunFlowTests.private_payload
    _headers = fixtures.PersonalPrivateRunFlowTests._headers
    _display_headers = fixtures.PersonalPrivateRunFlowTests._display_headers
    _create = fixtures.PersonalPrivateRunFlowTests._create

    def setUp(self):
        fixtures.PersonalPrivateRunFlowTests.setUp(self)
        for path in ("apps.agents.task_execution.close_old_connections", "apps.agents.runtime_services.close_old_connections"):
            patcher = mock.patch(path)
            patcher.start()
            self.addCleanup(patcher.stop)
        if hasattr(self, "configure_agent"):
            self.configure_agent()
        response = self._create("initial")
        self.assertEqual(response.status_code, 202, response.content)
        self.run_id = self.follow_payload(response)["run_id"]
        self.task = AgentExecutionTask.objects.get(run_id=self.run_id)
        self.headers = self._display_headers(self.run_id)
        self.path = f"/api/v1/agent-runs/{self.run_id}/follow-ups/"
        self.internal = f"/api/v1/internal/agent-runs/{self.run_id}/inbox/"
        self.token = json.loads(decrypt_secret(self.task.execution.encrypted_payload))["context"]["interaction_token"]

    def follow_payload(self, response):
        return response.json()

    def follow_thread_client(self):
        client = APIClient(enforce_csrf_checks=True)
        client.cookies.update(self.client.cookies)
        return client


class PersonalFollowUpFlowTests(PersonalFollowUpFixture, FollowUpStateGuards, TransactionTestCase):
    def test_queue_requires_owner_csrf_and_display_authority(self):
        body = {"mode": "queue", "content": "not authorized", "turn_index": 1, "idempotency_key": "denied"}
        self.assertEqual(APIClient().post(self.path, body, format="json", **self.headers).status_code, 401)
        no_csrf = {k: v for k, v in self.headers.items() if k != "HTTP_X_CSRFTOKEN"}
        self.assertEqual(self.client.post(self.path, body, format="json", **no_csrf).status_code, 403)
        wrong = {**self.headers, "HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN": "invalid"}
        self.assertEqual(self.client.post(self.path, body, format="json", **wrong).status_code, 404)
        self.assertFalse(AgentRunFollowUp.objects.exists())

    def test_other_owner_and_foreign_project_cannot_read_or_edit_receipts(self):
        response = self.send()
        self.assertEqual(response.status_code, 202, response.content)
        identity = self.follow_payload(response)["id"]
        other = get_user_model().objects.create_user(username="other-follow-up-owner")
        AccountProfile.objects.create(user=other, tenant_id=str(self.tenant.pk), project_id=str(self.project_a.pk), status="active")
        token = Token.objects.create(user=other)
        peer = APIClient()
        peer.credentials(HTTP_AUTHORIZATION="Bearer " + token.key)
        self.assertEqual(peer.get(self.path, {"idempotency_key": "input-1"}, **self.headers).status_code, 401)
        self.assertEqual(peer.patch(self.path + identity + "/", {"content": "forged"}, format="json", **self.headers).status_code, 401)
        self.assertEqual(self.client.get(self.path, **{**self.headers, "HTTP_X_NEXUS_PROJECT": "foreign"}).status_code, 403)
        self.assertEqual(AgentRunFollowUp.objects.get().content, "next")

    def test_turn_queue_capacity_and_internal_token_are_enforced(self):
        self.assertEqual(self.send(target=2).status_code, 409)
        for index in range(20):
            self.assertEqual(self.send(key=f"message-{index}").status_code, 202)
        self.assertEqual(self.send(key="too-many").status_code, 409)
        response = self.client.post(self.internal, {"action": "configure", "mode": "none", "turn_index": 1}, format="json",
            HTTP_X_NEXUS_INTERACTION_TOKEN="wrong")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(AgentRunFollowUp.objects.count(), 20)
