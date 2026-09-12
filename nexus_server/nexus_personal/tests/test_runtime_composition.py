"""Real owner/catalog/jobs/task-authority boundary, not full invocation E2E."""
from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
from types import SimpleNamespace
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import exceptions
from apps.accounts.models import AccountProfile
from apps.agents import services, runtime_services, task_execution, project_context
from apps.agents.context_extension import issue_run_context_extension
from apps.agents.models import Agent, AgentDisplayRun, AgentExecutionTask, AgentTaskExecution
from apps.agents.runtime_runner import RuntimeDisplayContext
from apps.common.subjects import request_subject
from apps.jobs.models import Job, JobEvent
from apps.tenancy.models import Tenant, Project
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


class PersonalRuntimeCompositionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="runtime-owner@example.test", password=PASSWORD)
        cls.other = get_user_model().objects.create_user(username="runtime-other")

    def request(self):
        return SimpleNamespace(user=self.row.owner, tenant_id=str(self.row.tenant_id),
            project_id=str(self.row.project_id), META={}, headers={}, query_params={})

    def agent(self):
        return services.create_agent(request=self.request(), name="personal-runtime")

    def payload(self):
        return {"subject": asdict(request_subject(self.request())), "tenant_id": str(self.row.tenant_id),
                "project_id": str(self.row.project_id), "service_token_id": "",
                "base_url": "https://personal.example/", "request_id": "task-request"}

    def test_runtime_owner_access_rejects_other_project_creator_and_disabled_agent(self):
        agent = self.agent()
        self.assertEqual(runtime_services.get_runtime_use_agent(request=self.request(), tenant=self.row.tenant, agent_id=str(agent.pk)).pk, agent.pk)
        self.assertEqual(runtime_services.runtime_actor_user(request=self.request(), api_key=None).pk, self.row.owner_id)
        with self.assertRaises(exceptions.AuthenticationFailed):
            runtime_services.runtime_actor_user(request=self.request(), api_key=object())
        agent.status = Agent.STATUS_DISABLED
        agent.save()
        with self.assertRaises(runtime_services.AgentRuntimeNotAvailable):
            runtime_services.get_runtime_use_agent(request=self.request(), tenant=self.row.tenant, agent_id=str(agent.pk))
        agent.status = Agent.STATUS_ACTIVE
        agent.created_by = self.other
        agent.save()
        with self.assertRaises(services.AgentNotFound):
            runtime_services.get_runtime_use_agent(request=self.request(), tenant=self.row.tenant, agent_id=str(agent.pk))

    def test_no_marketplace_fallback_for_public_foreign_agent(self):
        other_tenant = Tenant.objects.create(name="Foreign", slug="foreign-runtime")
        foreign = Agent.objects.create(tenant=other_tenant, created_by=self.other, name="Public foreign",
            visibility="public", publication_status="published", current_version="1")
        with self.assertRaises(services.AgentNotFound):
            runtime_services.get_runtime_use_agent(request=self.request(), tenant=self.row.tenant, agent_id=str(foreign.pk))

    def test_restore_request_retains_real_owner_subject_and_context(self):
        payload = self.payload()
        request = task_execution.restore_request(payload)
        self.assertEqual(request.user.pk, self.row.owner_id)
        self.assertEqual(asdict(request_subject(request)), payload["subject"])
        self.assertEqual(request.project_id, str(self.row.project_id))
        self.assertEqual(request.build_absolute_uri("/next"), "https://personal.example/next")
        self.assertIsNone(request.api_key)
        self.assertEqual(runtime_services.get_runtime_use_agent(request=request, tenant=self.row.tenant, agent_id=str(self.agent().pk)).created_by_id, self.row.owner_id)

    def test_saved_authority_rejects_forgery_and_context_changes(self):
        original = self.payload()
        mutations = [
            ("subject", "principal_type", "api_key"), ("subject", "principal_type", "service_account"),
            ("subject", "principal_id", str(self.other.pk)), ("subject", "subject_hash", "forged"),
            ("subject", "external", True), (None, "project_id", "foreign"),
            (None, "tenant_id", "foreign"), (None, "service_token_id", "foreign"),
        ]
        for section, field, value in mutations:
            payload = deepcopy(original)
            (payload[section] if section else payload)[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(exceptions.PermissionDenied):
                task_execution.restore_request(payload)
        AccountProfile.objects.filter(user=self.row.owner).update(status="disabled")
        with self.assertRaises(exceptions.AuthenticationFailed):
            task_execution.restore_request(original)

    def test_actual_jobs_serialize_runtime_operations_and_keep_history(self):
        agent = self.agent()
        job = Job.objects.create(tenant=self.row.tenant, project=self.row.project, created_by=self.row.owner,
            job_type="agents.runtime.deploy", resource_type="agent", resource_id=str(agent.pk),
            input_json={"agent_id": str(agent.pk), "env": "prod"})
        event = JobEvent.objects.create(tenant=self.row.tenant, job=job, event_type="queued", message="Queued")
        with self.assertRaises(runtime_services.AgentRuntimeOperationConflict):
            runtime_services.lock_runtime_operation(request=self.request(), agent_id=str(agent.pk), env="prod")
        job.status = Job.STATUS_SUCCEEDED
        job.save()
        self.assertEqual(runtime_services.lock_runtime_operation(request=self.request(), agent_id=str(agent.pk), env="prod").pk, agent.pk)
        self.assertTrue(JobEvent.objects.filter(pk=event.pk).exists())

    def test_personal_run_extension_has_no_financial_columns_or_synthetic_price(self):
        agent = self.agent()
        with self.assertRaises(exceptions.NotFound):
            issue_run_context_extension(agent=agent, tool_name="chat", now=timezone.now())
        agent.status = Agent.STATUS_ACTIVE
        agent.save(update_fields=["status"])
        extension = issue_run_context_extension(agent=agent, tool_name="chat", now=timezone.now())
        self.assertEqual(extension.model_values, {})
        self.assertEqual(extension.display_values("https://personal.example/run"), {})
        run = AgentDisplayRun.objects.create(agent=agent, tenant=self.row.tenant, consumer_tenant=self.row.tenant,
            consumer_project=self.row.project, run_kind="invocation", **extension.model_values)
        self.assertFalse(hasattr(run, "billing_token_hash"))
        context = RuntimeDisplayContext(run_id=str(run.pk), events_url="https://personal.example/events/",
            write_token="test-write-token", **extension.display_values("root"))
        self.assertEqual(context.billing_token, "")
        AccountProfile.objects.filter(user=self.row.owner).update(status="disabled")
        with self.assertRaises(exceptions.AuthenticationFailed):
            issue_run_context_extension(agent=agent, tool_name="chat", now=timezone.now())

    def test_project_context_uses_current_owner_and_preserves_snapshot(self):
        agent = self.agent()
        self.row.project.instructions_markdown = "Personal instructions"
        self.row.project.instructions_revision = 3
        self.row.project.save()
        snapshot = project_context.project_context_snapshot(request=self.request(), agent=agent)
        self.assertEqual(snapshot["instructions_markdown"], "Personal instructions")
        self.assertEqual(snapshot["instructions_revision"], 3)
        self.row.project.instructions_markdown = "Changed"
        self.row.project.save()
        self.assertEqual(snapshot["instructions_markdown"], "Personal instructions")

    def test_cancel_and_lost_attempt_revoke_core_delegates_without_billing_columns(self):
        agent = self.agent()
        future = timezone.now() + timedelta(hours=1)
        fields = {name + "_expires_at": future for name in (
            "workspace_delegate_token", "browser_delegate_token", "mobile_delegate_token", "interaction_token")}
        run = AgentDisplayRun.objects.create(agent=agent, tenant=self.row.tenant, consumer_tenant=self.row.tenant,
            consumer_project=self.row.project, run_kind="invocation", status="running", **fields)
        task = AgentExecutionTask.objects.create(run=run, agent=agent, tool_name="chat", expires_at=future,
            caller_subject_hash=request_subject(self.request()).subject_hash)
        execution = AgentTaskExecution.objects.create(task=task, state="running")
        AccountProfile.objects.filter(user=self.row.owner).update(status="disabled")
        task_execution.request_cancel(task)
        run.refresh_from_db()
        task.refresh_from_db()
        execution.refresh_from_db()
        self.assertEqual(task.status, "cancel_requested")
        self.assertIsNotNone(execution.cancel_requested_at)
        for name in ("workspace_delegate_token", "browser_delegate_token", "mobile_delegate_token"):
            self.assertLessEqual(getattr(run, name + "_expires_at"), timezone.now())
        self.assertEqual(run.interaction_token_expires_at, future)
        now = timezone.now()
        task_execution._revoke_attempt_tokens(run.pk, now)
        run.refresh_from_db()
        self.assertEqual(run.interaction_token_expires_at, now)

    def test_worker_sweep_executes_real_cleanup_without_private_capacity_imports(self):
        from django.db import connection
        agent = self.agent()
        now = timezone.now()
        values = dict(agent=agent, tenant=self.row.tenant, consumer_tenant=self.row.tenant,
                      consumer_project=self.row.project, status='running', write_token='test-only')
        # Exercise a real expired invocation envelope. Legacy/Public Demo
        # lifecycle belongs to the Enterprise host, not Personal capacity.
        stale = AgentDisplayRun.objects.create(**values, run_kind='invocation',
            context_token_hash='test-context-hash', context_token_expires_at=now + timedelta(hours=1))
        task = AgentExecutionTask.objects.create(run=stale, agent=agent, tool_name='chat',
            status='working', expires_at=now - timedelta(seconds=1),
            caller_subject_hash=request_subject(self.request()).subject_hash)
        execution = AgentTaskExecution.objects.create(task=task, state='queued', encrypted_payload='test-only-payload')
        legacy = AgentDisplayRun.objects.create(**values, run_kind='legacy')
        AgentDisplayRun.objects.filter(pk=legacy.pk).update(updated_at=now - timedelta(hours=2))
        recent = AgentDisplayRun.objects.create(**values, run_kind='legacy')
        invocation = AgentDisplayRun.objects.create(**values, run_kind='invocation')
        self.assertEqual(task_execution.sweep(), 1)
        stale.refresh_from_db()
        task.refresh_from_db()
        execution.refresh_from_db()
        self.assertEqual(stale.status, 'failed')
        self.assertEqual(stale.context_token_hash, '')
        self.assertIsNone(stale.context_token_expires_at)
        self.assertEqual(task.status, 'failed')
        self.assertEqual(task.error_code, 'TASK_DEADLINE_EXCEEDED')
        self.assertEqual(execution.state, 'failed')
        self.assertEqual(execution.encrypted_payload, '')
        self.assertEqual(stale.events.filter(event_type='RUN_ERROR').count(), 1)
        self.assertEqual(task_execution.sweep(), 0)
        for run in (legacy, recent, invocation):
            run.refresh_from_db()
            self.assertEqual(run.status, 'running')
            self.assertEqual(run.write_token, 'test-only')
            self.assertFalse(run.events.exists())
        self.assertFalse(any(name.startswith('billing_') for name in connection.introspection.table_names()))

    def test_capacity_recovery_summary_uses_measured_personal_limits(self):
        from apps.agents.run_capacity import capacity_summary
        from apps.common.resource_limits import capability_state
        from django.test import override_settings
        from django.core.exceptions import ImproperlyConfigured
        agent = self.agent()
        AgentDisplayRun.objects.create(agent=agent, tenant=self.row.tenant,
            consumer_tenant=self.row.tenant, run_kind='invocation', status='running')
        state = capability_state(tenant=self.row.tenant, code='agents.concurrent_runs')
        self.assertEqual(capacity_summary(agent), {key: state[key] for key in ('used', 'limit', 'remaining', 'state')})
        self.assertEqual(state['used'], 1)
        foreign = Tenant.objects.create(name='Foreign capacity', slug='foreign-capacity')
        with self.assertRaises(exceptions.NotFound):
            capacity_summary(SimpleNamespace(tenant=foreign))
        with override_settings(NEXUS_RESOURCE_ADMISSION_BACKEND=''):
            with self.assertRaises(ImproperlyConfigured):
                capacity_summary(agent)
