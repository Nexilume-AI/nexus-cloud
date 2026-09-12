"""Personal invocation expiry and rejected legacy callbacks; no fake runner."""
import json
from datetime import timedelta
from types import SimpleNamespace

from django.test import TestCase
from django.utils import timezone

from apps.agents import runtime_services, task_execution
from apps.agents.models import AgentDisplayRun, AgentExecutionTask, AgentTaskExecution
from apps.agents.policy import invoke_agent_policy
from apps.agents.run_capacity import expire_stale_non_invocation_runs
from apps.common.crypto import decrypt_secret
from . import test_conversation_http as conversation


class PersonalRunExpiryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        conversation.PersonalConversationHTTPTests.setUpTestData.__func__(cls)

    def setUp(self):
        conversation.PersonalConversationHTTPTests.setUp(self)
        conversation.PersonalConversationHTTPTests.start(self)
        self.execution = AgentTaskExecution.objects.get(task__run=self.run)
        self.task = self.execution.task
        self.context = json.loads(decrypt_secret(self.execution.encrypted_payload))["context"]

    def assert_failed(self, code):
        self.run.refresh_from_db()
        self.task.refresh_from_db()
        self.execution.refresh_from_db()
        self.assertEqual((self.run.status, self.task.status, self.execution.state),
                         ("failed", "failed", "failed"))
        self.assertEqual(self.task.error_code, code)
        self.assertEqual(self.execution.encrypted_payload, "")
        self.assertIsNone(self.execution.lease_expires_at)
        self.assertEqual(self.run.context_token_hash, "")
        self.assertIsNone(self.run.context_token_expires_at)
        self.assertEqual(self.run.runtime_invocations.get().status, "failed")
        self.assertIsNotNone(self.run.completed_at)
        self.assertEqual(self.run.messages.filter(role="user").first().content, "First turn")
        self.assertEqual(self.run.events.filter(event_type="RUN_ERROR").count(), 1)

    def test_expired_real_queued_invocation_fails_and_preserves_history(self):
        AgentExecutionTask.objects.filter(pk=self.task.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(task_execution.sweep(), 1)
        self.assert_failed("TASK_DEADLINE_EXCEEDED")
        self.assertEqual(task_execution.sweep(), 0)
        self.assertEqual(self.run.events.filter(event_type="RUN_ERROR").count(), 1)

    def test_unexpired_invocation_is_not_closed_by_shared_sweep(self):
        before = self.execution.encrypted_payload
        self.assertEqual(task_execution.sweep(), 0)
        self.run.refresh_from_db()
        self.execution.refresh_from_db()
        self.assertEqual(self.run.status, "running")
        self.assertEqual(self.execution.state, "queued")
        self.assertEqual(self.execution.encrypted_payload, before)
        self.assertEqual(self.run.runtime_invocations.get().status, "pending")

    def test_claimed_cancellation_deadline_is_finalized_without_replay(self):
        task_id, lease_id = task_execution.claim()
        self.assertEqual(task_id, str(self.task.pk))
        task_execution.request_cancel(self.task)
        AgentTaskExecution.objects.filter(task=self.task).update(
            cancel_requested_at=timezone.now() - timedelta(seconds=61))
        self.assertEqual(task_execution.sweep(), 1)
        self.assert_failed("CANCEL_UNCONFIRMED")
        self.assertFalse(task_execution.heartbeat(task_id, lease_id))

    def test_private_legacy_cleanup_is_not_run_or_queried_in_personal(self):
        for kind in ("demo", "legacy"):
            AgentDisplayRun.objects.filter(pk=self.run.pk).update(
                run_kind=kind, updated_at=timezone.now() - timedelta(hours=2),
                interaction_token_expires_at=timezone.now() - timedelta(seconds=1))
            with self.subTest(kind=kind), self.assertNumQueries(0):
                self.assertEqual(expire_stale_non_invocation_runs(), 0)
            self.run.refresh_from_db()
            self.assertEqual(self.run.status, "running")
            self.assertEqual(self.run.events.filter(event_type="RUN_ERROR").count(), 0)

    def test_valid_tokens_cannot_admit_forged_demo_or_legacy_callbacks(self):
        token = self.context["interaction_token"]
        for kind in ("demo", "legacy"):
            AgentDisplayRun.objects.filter(pk=self.run.pk).update(run_kind=kind)
            self.run.refresh_from_db()
            calls = (
                lambda: runtime_services.get_internal_interaction_run(run_id=str(self.run.pk), token=token),
                lambda: runtime_services.create_run_interaction(run_id=str(self.run.pk), token=token,
                    data={"key": "denied", "prompt": "Must not create"}),
                lambda: runtime_services.create_run_display_asset(run_id=str(self.run.pk), token=token, data={}),
                lambda: invoke_agent_policy("validate_run_interaction", run=self.run),
                lambda: invoke_agent_policy("run_display_asset_url", run=self.run, asset=SimpleNamespace(id="denied")),
            )
            for index, call in enumerate(calls):
                with self.subTest(kind=kind, operation=index), self.assertRaises(runtime_services.AgentRuntimeNotFound):
                    call()
            self.assertFalse(self.run.interactions.exists())

    def test_demo_transport_cannot_enable_personal_interaction(self):
        AgentDisplayRun.objects.filter(pk=self.run.pk).update(interaction_mode="demo")
        with self.assertRaises(runtime_services.AgentRuntimeError) as caught:
            runtime_services.create_run_interaction(run_id=str(self.run.pk),
                token=self.context["interaction_token"], data={"key": "denied", "prompt": "Must not create"})
        self.assertEqual(caught.exception.default_code, "INTERACTIVE_TRANSPORT_REQUIRED")
        self.assertFalse(self.run.interactions.exists())
