"""Portable source, recovery and query-bound notification assertions."""
from datetime import timedelta

from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.agents.models import AgentPythonBuild, AgentRunInteraction
from apps.jobs.models import Job
from apps.notifications.models import UserNotification


class NotificationSourceGuards:
    def test_transaction_rollback_leaves_no_phantom_notice(self):
        with self.assertRaises(RuntimeError), transaction.atomic():
            self.new_run()
            raise RuntimeError("rollback")
        self.assertFalse(UserNotification.objects.exists())

    def test_only_human_invocations_produce_run_notices(self):
        self.new_run(run_kind="demo")
        self.new_run(caller_principal_type="api_key")
        self.assertFalse(UserNotification.objects.exists())

    def test_reconciliation_is_idempotent_and_does_not_flood_old_completions(self):
        from django.core.management import call_command
        from io import StringIO
        self.new_run()
        run = self.new_run(status="running", completed_at=None)
        question = AgentRunInteraction.objects.create(run=run, key="q", prompt="private", expires_at=timezone.now() + timedelta(minutes=5))
        UserNotification.objects.all().delete()
        for _ in range(2):
            call_command("reconcile_notifications", stdout=StringIO())
        self.assertEqual(UserNotification.objects.count(), 1)
        self.assertEqual(UserNotification.objects.get().interaction_id, question.pk)
        self.assertEqual(run.status, "running")


    def test_run_query_count_is_not_per_notification(self):
        self.new_run()
        with CaptureQueriesContext(connection) as small:
            self.listing()
        for _ in range(12):
            self.new_run()
        with CaptureQueriesContext(connection) as large:
            self.listing()
        self.assertLessEqual(len(large), len(small) + 1)

    def test_real_event_service_records_completion_and_failure_without_payload(self):
        from apps.agents.models import AgentDisplayEvent
        from apps.agents.services import append_display_event
        for event_type in [AgentDisplayEvent.TYPE_RUN_COMPLETED, AgentDisplayEvent.TYPE_RUN_FAILED]:
            run = self.new_run(status="running", completed_at=None)
            payload = {"message": "SECRET_DIAGNOSTIC"} if event_type == AgentDisplayEvent.TYPE_RUN_FAILED else {}
            append_display_event(run=run, event_type=event_type, payload=payload, client_event_id="one-event")
            append_display_event(run=run, event_type=event_type, payload=payload, client_event_id="one-event")
        self.assertEqual(UserNotification.objects.count(), 2)
        data = self.listing()
        self.assertEqual(data["counts"], {"unread": 2, "needs_attention": 1, "total": 2})
        self.assertNotIn("SECRET_DIAGNOSTIC", str(data))

    def test_real_job_service_records_failure_idempotently(self):
        from apps.jobs.services import fail_job
        job = Job.objects.create(tenant=self.tenant, created_by=self.user, job_type="agents.runtime.deploy", resource_type="agent", resource_id=str(self.agent.pk))
        for _ in range(2):
            fail_job(job_id=job.pk, error_code="FAILED", error_message="SECRET_JOB_ERROR")
        self.assertEqual(UserNotification.objects.count(), 1)
        self.assertEqual(self.listing()["results"][0]["kind"], self.notification_deployment_failure_kind)
        self.assertNotIn("SECRET_JOB_ERROR", str(self.listing()))

    def test_arbitrary_jobs_and_malformed_source_ids_do_not_create_notices(self):
        for kind, resource in [("providers.runtime.login", str(self.agent.pk)), ("agents.runtime.deploy", "not-a-uuid")]:
            Job.objects.create(tenant=self.tenant, created_by=self.user, job_type=kind, resource_type="agent", resource_id=resource,
                status="failed", completed_at=timezone.now())
        self.assertEqual(UserNotification.objects.count(), 0)


class NotificationBuildWorkerGuards:
    def test_build_worker_loss_generates_notice(self):
        from apps.agents.python_builds import claim_build
        AgentPythonBuild.objects.create(agent=self.agent, created_by=self.user, status="running", lease_expires_at=timezone.now() - timedelta(seconds=1))
        claim_build(worker_id="notification-test")
        self.assertEqual(UserNotification.objects.get().kind, "build_failed")
