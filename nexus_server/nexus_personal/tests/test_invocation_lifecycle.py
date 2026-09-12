"""Real context/events/receipt lifecycle, without a mocked wallet or runner."""
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import exceptions

from apps.agents.models import Agent, AgentDisplayRun, AgentExecutionTask, AgentRuntimeDeployment, AgentRuntimeInvocation, AgentRuntimeImage, AgentDisplayEvent, AgentTaskExecution
from apps.agents import runtime_services, services, task_execution
from apps.common.invocation_lifecycle import begin_runtime_invocation, finalize_runtime_invocation, invocation_preflight
from apps.common.resource_limits import capability_state
from apps.audit.models import AuditLog
from nexus_personal.agent_invocations import PersonalInvocationLifecycle
from nexus_personal.models import PersonalInstallation, PersonalInvocationUsage
from nexus_personal.resource_limits import PersonalCapacityExceeded
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


class PersonalInvocationLifecycleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="lifecycle@example.test", password=PASSWORD)
        cls.agent = Agent.objects.create(tenant=cls.row.tenant, project=cls.row.project,
            created_by=cls.row.owner, name="Lifecycle", status="active")
        image = AgentRuntimeImage.objects.create(tenant=cls.row.tenant, project=cls.row.project,
            agent=cls.agent, image_ref="personal-lifecycle:unit", created_by=cls.row.owner)
        cls.runtime = AgentRuntimeDeployment.objects.create(tenant=cls.row.tenant, project=cls.row.project,
            agent=cls.agent, image=image, status="active", health_status="healthy")

    def request(self):
        return SimpleNamespace(user=self.row.owner, tenant_id=str(self.row.tenant_id),
            project_id=str(self.row.project_id), META={}, headers={}, query_params={},
            request_id="personal-lifecycle", build_absolute_uri=lambda path: "https://personal.test" + path)

    def context(self):
        return runtime_services.create_invocation_display_context(runtime=self.runtime, tool_name="echo", request=self.request())

    def begin(self, run, turn=1, **changes):
        values = dict(request=self.request(), tenant=self.row.tenant, agent=self.agent,
            runtime=self.runtime, api_key=None, tool_name="echo", display_run=run, turn_index=turn)
        return begin_runtime_invocation(**{**values, **changes})

    def finish(self, invocation, **changes):
        return finalize_runtime_invocation(**{**dict(request=self.request(), invocation=invocation,
            succeeded=True, error_code="", latency_ms=1000), **changes})

    def usage(self, code="agents.runs_per_30_days"):
        return capability_state(tenant=self.row.tenant, code=code)

    def limits(self, **changes):
        return override_settings(NEXUS_PERSONAL_AGENT_LIMITS={**settings.NEXUS_PERSONAL_AGENT_LIMITS, **changes})

    def test_real_context_and_successful_finish_keep_nonfinancial_receipt(self):
        invocation_preflight(tenant=self.row.tenant, agent=self.agent)
        run, context = self.context()
        self.assertEqual(context.billing_token, "")
        self.assertTrue(run.events.filter(event_type="RUN_STARTED").exists())
        invocation = self.begin(run)
        self.assertEqual(invocation.status, "pending")
        self.assertFalse(hasattr(invocation, "cost"))
        self.assertEqual(self.usage()["used"], 1)
        self.assertGreater(self.usage("agents.run_minutes_per_30_days")["used"], 0)
        services.append_display_event(run=run, event_type="TEXT_MESSAGE_START", payload={"messageId": "answer", "role": "assistant"})
        services.append_display_event(run=run, event_type="TEXT_MESSAGE_CONTENT", payload={"messageId": "answer", "delta": "Hello"})
        services.append_display_event(run=run, event_type="TEXT_MESSAGE_END", payload={"messageId": "answer"})
        runtime_services.finish_invocation_display_run(run=run, succeeded=True)
        completed = self.finish(invocation)
        run.refresh_from_db()
        self.assertEqual(run.status, "completed")
        self.assertEqual(completed.status, "success")
        self.assertEqual(self.usage("agents.run_minutes_per_30_days")["used"], Decimal(1) / 60)
        self.assertEqual(self.usage("agents.concurrent_runs")["used"], 0)
        self.assertTrue(run.events.filter(event_type=AgentDisplayEvent.TYPE_RUN_COMPLETED).exists())
        self.assertFalse(run.context_token_hash)

    def test_begin_and_finalization_replays_do_not_duplicate_usage_or_audit(self):
        run, _ = self.context()
        first = self.begin(run)
        self.assertEqual(self.begin(run).pk, first.pk)
        self.finish(first)
        self.finish(first, latency_ms=5000)
        self.assertEqual(PersonalInvocationUsage.objects.count(), 1)
        self.assertEqual(PersonalInvocationUsage.objects.get().latency_ms, 1000)
        self.assertEqual(AuditLog.objects.filter(resource_id=str(self.agent.pk), action="agents.runtime.invoke").count(), 1)
        with self.assertRaises(exceptions.ValidationError):
            self.begin(run, tool_name="forged")

    def test_continued_turn_has_independent_receipt_and_rejects_old_turn_finalization(self):
        run, _ = self.context()
        first = self.begin(run)
        self.finish(first, succeeded=False, error_code="DISCONNECTED")
        task = AgentExecutionTask.objects.create(agent=self.agent, runtime=self.runtime,
            run=run, tool_name="echo", status="working", expires_at=timezone.now() + timedelta(hours=1),
            request_json={"turn_index": 2})
        second = self.begin(run, turn=2)
        self.assertNotEqual(second.pk, first.pk)
        self.assertEqual(self.usage()["used"], 2)
        with self.assertRaises(exceptions.NotFound):
            self.finish(first)
        self.finish(second)
        self.assertEqual(PersonalInvocationUsage.objects.filter(finished_at__isnull=False).count(), 2)

    def test_period_count_and_minutes_block_dispatch_and_close_already_created_context(self):
        run, _ = self.context()
        with self.limits(**{"agents.runs_per_30_days": 0}), self.assertRaises(PersonalCapacityExceeded):
            self.begin(run)
        run.refresh_from_db()
        self.assertEqual(run.status, "failed")
        self.assertFalse(run.write_token)
        self.assertFalse(AgentRuntimeInvocation.objects.exists())
        run, _ = self.context()
        with self.limits(**{"agents.run_minutes_per_30_days": 0}), self.assertRaises(PersonalCapacityExceeded):
            self.begin(run)
        self.assertEqual(self.usage("agents.concurrent_runs")["used"], 0)

    def test_pending_receipts_reserve_minutes_before_another_dispatch(self):
        with self.limits(**{"agents.run_minutes_per_run": 1, "agents.run_minutes_per_30_days": 1}):
            run, _ = self.context()
            first = self.begin(run)
            next_run, _ = self.context()
            with self.assertRaises(PersonalCapacityExceeded):
                self.begin(next_run)
            self.finish(first, latency_ms=0)
            final_run, _ = self.context()
            self.begin(final_run)

    def test_usage_survives_deletion_and_resets_on_installation_period(self):
        run, _ = self.context()
        self.finish(self.begin(run))
        run.delete()
        AgentRuntimeInvocation.objects.all().delete()
        self.assertEqual(self.usage()["used"], 1)
        self.assertIsNone(PersonalInvocationUsage.objects.get().invocation_id)
        now = timezone.now()
        PersonalInstallation.objects.update(created_at=now - timedelta(days=35))
        PersonalInvocationUsage.objects.update(started_at=now - timedelta(days=10))
        self.assertEqual(self.usage()["used"], 0)
        self.assertGreater(self.usage()["reset_at"], now)

    def test_stale_cleanup_fails_run_and_releases_reserved_minutes_once(self):
        run, _ = self.context()
        invocation = self.begin(run)
        PersonalInvocationUsage.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        from nexus_personal.tasks import expire_invocation_leases
        self.assertEqual(expire_invocation_leases(), 1)
        self.assertEqual(expire_invocation_leases(), 0)
        run.refresh_from_db()
        invocation.refresh_from_db()
        self.assertEqual(run.status, "failed")
        self.assertEqual(invocation.status, "failed")
        self.assertEqual(invocation.error_code, "INVOCATION_EXPIRED")
        self.assertEqual(self.usage("agents.concurrent_runs")["used"], 0)
        self.assertFalse(run.context_token_hash)

    def test_foreign_actor_wrong_turn_and_lost_worker_lease_fail_closed(self):
        run, _ = self.context()
        for changes in ({"api_key": object()}, {"turn_index": 2}):
            with self.assertRaises(exceptions.APIException):
                self.begin(run, **changes)
        other = get_user_model().objects.create_user(username="other-lifecycle")
        request = self.request()
        request.user = other
        with self.assertRaises(exceptions.AuthenticationFailed):
            self.begin(run, request=request)
        invocation = self.begin(run)
        lease = task_execution._lease.set((uuid4(), uuid4()))
        try:
            with self.assertRaises(task_execution.ExecutionFenced):
                self.finish(invocation)
        finally:
            task_execution._lease.reset(lease)

    def test_pricing_and_reports_are_absent_not_synthetic_free_plans(self):
        backend = PersonalInvocationLifecycle()
        for name in ("pricing_snapshot", "estimate", "report"):
            with self.assertRaises(exceptions.NotFound):
                getattr(backend, name)(agent=self.agent)

    def test_durable_execution_is_owned_by_task_reaper_and_renewal_is_bounded(self):
        run, _ = self.context()
        invocation = self.begin(run)
        receipt = PersonalInvocationUsage.objects.get(invocation=invocation)
        backend = PersonalInvocationLifecycle()
        backend.renew_lease(run=run, expires_at=timezone.now() + timedelta(days=10))
        receipt.refresh_from_db()
        self.assertLessEqual(receipt.expires_at,
            receipt.started_at + timedelta(milliseconds=receipt.reserved_ms, minutes=5))
        task = AgentExecutionTask.objects.create(agent=self.agent, run=run, runtime=self.runtime,
            tool_name="echo", expires_at=timezone.now() + timedelta(hours=1), request_json={"turn_index": 1})
        AgentTaskExecution.objects.create(task=task, state="running")
        PersonalInvocationUsage.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(backend.release_stale(), 0)
        invocation.refresh_from_db()
        self.assertEqual(invocation.status, "pending")
        with self.assertRaises(exceptions.ValidationError):
            backend.renew_lease(run=run, expires_at=timezone.now() + timedelta(hours=1))

    def test_expired_context_without_dispatch_recovers_its_concurrent_slot(self):
        run, _ = self.context()
        AgentDisplayRun.objects.filter(pk=run.pk).update(created_at=timezone.now() - timedelta(minutes=10))
        backend = PersonalInvocationLifecycle()
        self.assertEqual(backend.release_stale(), 1)
        self.assertEqual(backend.release_stale(), 0)
        run.refresh_from_db()
        self.assertEqual(run.status, "failed")
        self.assertFalse(run.context_token_hash)
        self.assertFalse(PersonalInvocationUsage.objects.exists())

    def test_real_durable_enqueue_claim_heartbeat_and_cancel_finalize_receipt(self):
        run, context = self.context()
        task = AgentExecutionTask.objects.create(agent=self.agent, run=run, runtime=self.runtime,
            tool_name="echo", caller_subject_hash=run.caller_subject_hash,
            expires_at=timezone.now() + timedelta(hours=1), request_json={"turn_index": 1})
        task_execution.enqueue(task=task, request=self.request(), body=b"private-test-input",
            headers={"Authorization": "private-test-header"}, display_pair=(run, context))
        execution = AgentTaskExecution.objects.get(task=task)
        self.assertEqual(execution.state, "queued")
        self.assertNotIn("private-test-input", execution.encrypted_payload)
        self.assertNotIn("private-test-header", execution.encrypted_payload)
        task_id, lease_id = task_execution.claim()
        self.assertEqual(task_id, str(task.pk))
        # Extension failures must roll back the heartbeat lease, not leave a
        # partial renewal or silently turn an unavailable host into a grant.
        from unittest.mock import patch
        from django.core.exceptions import ImproperlyConfigured
        from nexus_personal.agent_runtime_policy import PersonalRunContextExtension
        from apps.agents.context_extension import renewed_run_context_fields
        execution.refresh_from_db()
        original_lease = execution.lease_expires_at
        with patch.object(PersonalRunContextExtension, 'renewed_model_values', return_value=[]):
            with self.assertRaises(ImproperlyConfigured):
                task_execution.heartbeat(task_id, lease_id)
        execution.refresh_from_db()
        self.assertEqual(execution.lease_expires_at, original_lease)
        self.assertEqual(renewed_run_context_fields(run=run, expires_at=timezone.now()), {})
        self.assertTrue(task_execution.heartbeat(task_id, lease_id))
        run.refresh_from_db()
        self.assertGreater(run.interaction_token_expires_at, timezone.now())
        self.assertLessEqual(run.interaction_token_expires_at, task.expires_at)
        task_execution.request_cancel(task)
        task_execution.terminate(task_id, "RUN_CANCELLED", cancelled=True, lease_id=lease_id)
        execution.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(execution.state, "cancelled")
        self.assertFalse(execution.encrypted_payload)
        self.assertEqual(run.status, "failed")
        invocation = AgentRuntimeInvocation.objects.get(display_run=run)
        self.assertEqual(invocation.status, "failed")
        self.assertEqual(invocation.error_code, "RUN_CANCELLED")
        self.assertIsNotNone(PersonalInvocationUsage.objects.get(invocation=invocation).finished_at)
        self.assertEqual(self.usage("agents.concurrent_runs")["used"], 0)
