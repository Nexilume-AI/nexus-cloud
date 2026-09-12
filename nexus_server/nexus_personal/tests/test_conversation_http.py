"""Actual conversation HTTP, queue and authority persistence, not Agent execution.

Successful completion is an explicit persisted result fixture. These tests do
not claim Docker/Browser/Computer acceptance and install no fake runner.
"""
import json
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.agents import runtime_services, task_execution, follow_ups
from apps.agents.models import AgentDisplayRun, AgentRuntimeInvocation, AgentTaskExecution, AgentExecutionTask
from apps.common.crypto import decrypt_secret, encrypt_secret
from nexus_personal.models import PersonalInvocationUsage
from tests.agent_task_arguments import queued_arguments
from . import test_agent_file_http as file_http


class PersonalConversationHTTPTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        file_http.PersonalAgentFileHTTPTests.setUpTestData.__func__(cls)
        cls.agent.repo_metadata = {"tools": [{"name": "chat", "description": "Conversation",
            "input_schema": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
            "task": True, "chat": True, "continuable": True, "interactive": True}]}
        cls.agent.save(update_fields=["repo_metadata"])

    def setUp(self):
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.token.key)
        self.agent_base = f"/api/v1/agents/{self.agent.pk}/"

    def start(self):
        response = self.client.post(self.agent_base + "private-runs/", {"message": "First turn"}, format="json")
        self.assertEqual(response.status_code, 202, response.data)
        self.run = AgentDisplayRun.objects.get(pk=response.data["run_id"])
        self.base = f"/api/v1/agent-runs/{self.run.pk}/"
        token = self.client.post(self.base + "display-token/", {}, format="json")
        self.assertEqual(token.status_code, 200, token.data)
        self.headers = {"HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN": token.data["display_token"]}
        return response

    def envelope(self):
        return AgentTaskExecution.objects.get(task__run=self.run)

    def queue(self, key="queued", content="Next instruction"):
        return self.client.post(self.base + "follow-ups/", {"mode": "queue", "content": content,
            "turn_index": 1, "idempotency_key": key}, format="json", **self.headers)

    def settle_success_fixture(self):
        # A completed handler/result fixture, not evidence of external execution.
        invocation = self.run.runtime_invocations.order_by("-turn_index").first()
        request = task_execution.restore_request(json.loads(decrypt_secret(self.envelope().encrypted_payload)))
        runtime_services.finalize_runtime_invocation(request=request, invocation=invocation,
            succeeded=True, error_code="", latency_ms=100)
        runtime_services.finish_invocation_display_run(run=self.run, succeeded=True)
        AgentExecutionTask.objects.filter(run=self.run).update(status="completed", completed_at=timezone.now())
        AgentTaskExecution.objects.filter(task__run=self.run).update(state="finished", encrypted_payload="", lease_expires_at=None)
        self.run.refresh_from_db()

    def test_interactor_and_start_preserve_real_queue_context_and_owner_history(self):
        catalog = self.client.get(self.agent_base + "interactor/")
        self.assertEqual(catalog.status_code, 200, catalog.data)
        self.assertEqual(catalog.data["default_tool_name"], "chat")
        self.assertNotIn("pricing", catalog.data)
        response = self.start()
        self.assertEqual(response.data["run"]["messages"][0]["content"], "First turn")
        payload = json.loads(decrypt_secret(self.envelope().encrypted_payload))
        self.assertEqual(payload["context"]["run_id"], str(self.run.pk))
        self.assertEqual(self.envelope().state, "queued")
        self.assertEqual(AgentRuntimeInvocation.objects.get().status, "pending")
        self.assertEqual(PersonalInvocationUsage.objects.count(), 1)
        history = self.client.get(self.agent_base + "private-runs/")
        self.assertEqual(history.status_code, 200, history.data)
        self.assertEqual([r["id"] for r in history.data["results"]], [str(self.run.pk)])
        self.assertNotIn(payload["context"]["interaction_token"], str(history.data))

    def test_failed_turn_resumes_same_run_with_new_delegates_and_usage(self):
        self.start()
        old = json.loads(decrypt_secret(self.envelope().encrypted_payload))["context"]
        task_execution.terminate(str(self.envelope().task_id), "AGENT_CONNECTION_LOST")
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "failed")
        response = self.client.post(self.base + "resume/", {"message": "Try after reconnect"}, format="json", **self.headers)
        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(response.data["run_id"], str(self.run.pk))
        self.assertEqual(response.data["run"]["turn_index"], 2)
        current = json.loads(decrypt_secret(self.envelope().encrypted_payload))["context"]
        for field in ("interaction_token", "context_token", "write_token"):
            self.assertNotEqual(current[field], old[field])
        self.assertEqual(list(self.run.messages.values_list("content", flat=True)), ["First turn", "Try after reconnect"])
        self.assertEqual(list(self.run.runtime_invocations.order_by("turn_index").values_list("status", flat=True)), ["failed", "pending"])
        self.assertEqual(PersonalInvocationUsage.objects.count(), 2)
        retry = self.client.post(self.base + "resume/", {"message": "Duplicate"}, format="json", **self.headers)
        self.assertEqual(retry.status_code, 409)
        self.assertEqual(PersonalInvocationUsage.objects.count(), 2)

    def test_shared_queue_arguments_restore_actual_body_owner_and_context(self):
        self.start()
        envelope = self.envelope()
        ciphertext = envelope.encrypted_payload
        values = queued_arguments(envelope.task)
        self.assertEqual(values["task_id"], str(envelope.task_id))
        self.assertEqual(values["agent_id"], str(self.agent.pk))
        self.assertEqual(json.loads(values["body"])["params"]["arguments"]["message"], "First turn")
        self.assertEqual(values["request"].user.pk, self.row.owner_id)
        self.assertEqual(values["request"].tenant_id, str(self.row.tenant_id))
        self.assertEqual(values["request"].project_id, str(self.row.project_id))
        self.assertIsNone(values["request"].api_key)
        self.assertEqual(values["display_pair"][0].pk, self.run.pk)
        self.assertEqual(values["display_pair"][1].run_id, str(self.run.pk))
        self.assertEqual(self.envelope().encrypted_payload, ciphertext)
        self.assertEqual(self.envelope().state, "queued")
        self.assertEqual(PersonalInvocationUsage.objects.count(), 1)

    def test_shared_queue_arguments_reject_corrupt_ciphertext_and_revoked_owner(self):
        from cryptography.fernet import InvalidToken
        from rest_framework.exceptions import PermissionDenied
        self.start()
        envelope = self.envelope()
        ciphertext = envelope.encrypted_payload
        envelope.encrypted_payload = "invalid-encrypted-test-fixture"
        envelope.save(update_fields=["encrypted_payload"])
        with self.assertRaises(InvalidToken):
            queued_arguments(self.envelope().task)
        envelope.encrypted_payload = ciphertext
        envelope.save(update_fields=["encrypted_payload"])
        self.row.owner.is_active = False
        self.row.owner.save(update_fields=["is_active"])
        with self.assertRaises(PermissionDenied):
            queued_arguments(self.envelope().task)
        self.assertEqual(self.envelope().state, "queued")
        self.assertEqual(PersonalInvocationUsage.objects.count(), 1)

    def test_queue_idempotency_edit_then_real_dispatch_uses_original_run(self):
        self.start()
        response = self.queue()
        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(self.queue().data["id"], response.data["id"])
        self.assertEqual(self.queue(content="Different").status_code, 409)
        row = self.run.follow_ups.get()
        authority = json.loads(decrypt_secret(row.encrypted_authority))
        self.assertEqual(authority["invocation_authority"]["id"], str(self.run.runtime_invocations.get().pk))
        self.assertNotIn("pricing", authority)
        revision = self.client.get(self.base + "follow-ups/", **self.headers).data["queue_revision"]
        edited = self.client.patch(self.base + f"follow-ups/{row.pk}/", {"content": "Edited next turn", "expected_revision": revision}, format="json", **self.headers)
        self.assertEqual(edited.status_code, 200, edited.data)
        self.settle_success_fixture()
        self.assertTrue(follow_ups.dispatch(self.run.pk))
        row.refresh_from_db()
        self.assertEqual((row.status, row.dispatched_turn, row.encrypted_authority), ("dispatched", 2, ""))
        self.assertEqual(self.run.messages.order_by("-sequence").first().content, "Edited next turn")
        self.assertFalse(follow_ups.dispatch(self.run.pk))
        self.assertEqual(self.run.runtime_invocations.count(), 2)

    def test_failed_prior_turn_and_context_changes_do_not_automatically_replay(self):
        self.start()
        self.assertEqual(self.queue().status_code, 202)
        task_execution.terminate(str(self.envelope().task_id), "AGENT_CONNECTION_LOST")
        self.assertFalse(follow_ups.dispatch(self.run.pk))
        row = self.run.follow_ups.get()
        self.assertEqual((row.status, row.code, row.encrypted_authority), ("blocked", "PREVIOUS_TURN_FAILED", ""))
        self.assertEqual(self.run.runtime_invocations.count(), 1)

    def test_changed_computer_blocks_queued_turn_without_consuming_capacity(self):
        self.start()
        self.assertEqual(self.queue().status_code, 202)
        self.settle_success_fixture()
        self.run.computer_revision += 1
        self.run.save(update_fields=["computer_revision"])
        self.assertFalse(follow_ups.dispatch(self.run.pk))
        self.assertEqual(self.run.follow_ups.get().code, "FOLLOW_UP_CONTEXT_CHANGED")
        self.assertEqual(PersonalInvocationUsage.objects.count(), 1)

    def test_sdk_steer_receive_ack_and_caller_isolation(self):
        self.start()
        task_id, _ = task_execution.claim()
        self.assertEqual(task_id, str(self.envelope().task_id))
        context = json.loads(decrypt_secret(self.envelope().encrypted_payload))["context"]
        internal = APIClient()
        path = f"/api/v1/internal/agent-runs/{self.run.pk}/inbox/"
        headers = {"HTTP_X_NEXUS_INTERACTION_TOKEN": context["interaction_token"]}
        configured = internal.post(path, {"turn_index": 1, "action": "configure", "mode": "steer_and_queue"}, format="json", **headers)
        self.assertEqual(configured.status_code, 200, configured.data)
        submitted = self.client.post(self.base + "follow-ups/", {"mode": "steer", "content": "Adjust direction",
            "turn_index": 1, "idempotency_key": "steer-1"}, format="json", **self.headers)
        self.assertEqual(submitted.status_code, 202, submitted.data)
        received = internal.post(path, {"turn_index": 1, "action": "receive"}, format="json", **headers)
        self.assertEqual(received.status_code, 200, received.data)
        self.assertEqual(received.data["items"][0]["status"], "received")
        for _ in range(2):
            ack = internal.post(path, {"turn_index": 1, "action": "acknowledge", "message_id": submitted.data["id"]}, format="json", **headers)
            self.assertEqual(ack.status_code, 200, ack.data)
        self.assertEqual(self.run.messages.filter(content="Adjust direction").count(), 1)
        self.assertEqual(internal.post(path, {"turn_index": 2, "action": "receive"}, format="json", **headers).status_code, 409)
        self.assertEqual(APIClient().post(self.base + "resume/", {}, format="json").status_code, 401)
        self.assertEqual(self.client.get(self.base + "follow-ups/").status_code, 404)

    def test_foreign_invocation_snapshot_cannot_authorize_a_queued_turn(self):
        from uuid import uuid4
        self.start()
        self.assertEqual(self.queue().status_code, 202)
        row = self.run.follow_ups.get()
        authority = json.loads(decrypt_secret(row.encrypted_authority))
        authority["invocation_authority"]["id"] = str(uuid4())
        row.encrypted_authority = encrypt_secret(json.dumps(authority))
        row.save(update_fields=["encrypted_authority"])
        self.settle_success_fixture()
        self.assertFalse(follow_ups.dispatch(self.run.pk))
        row.refresh_from_db()
        self.assertEqual((row.status, row.code, row.encrypted_authority), ("blocked", "FOLLOW_UP_CONTEXT_CHANGED", ""))
        self.assertEqual(PersonalInvocationUsage.objects.count(), 1)

    def test_owner_disable_after_queue_blocks_dispatch_and_clears_authority(self):
        self.start()
        self.assertEqual(self.queue().status_code, 202)
        self.settle_success_fixture()
        self.row.owner.is_active = False
        self.row.owner.save(update_fields=["is_active"])
        self.assertFalse(follow_ups.dispatch(self.run.pk))
        row = self.run.follow_ups.get()
        self.assertEqual(row.status, "blocked")
        self.assertEqual(row.encrypted_authority, "")
        self.assertEqual(PersonalInvocationUsage.objects.count(), 1)
