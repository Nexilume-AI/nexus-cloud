"""Durable Task contracts with actual Personal owner/session/CSRF authority."""
from unittest import mock
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from apps.accounts.models import AccountProfile
from apps.agents import task_execution as worker
from apps.agents.models import AgentExecutionTask, AgentRuntimeInvocation, AgentTaskExecution
from nexus_personal.models import PersonalInvocationUsage
from tests.durable_task_guards import DurableTaskHelpers, DurableTaskGuards, TaskStreamGuards
from . import test_private_run_flow as fixtures


class PersonalDurableFixture(DurableTaskHelpers):
    authenticate_private_caller = fixtures.PersonalPrivateRunFlowTests.authenticate_private_caller
    create_private_runtime = fixtures.PersonalPrivateRunFlowTests.create_private_runtime
    private_payload = fixtures.PersonalPrivateRunFlowTests.private_payload
    _headers = fixtures.PersonalPrivateRunFlowTests._headers
    _display_headers = fixtures.PersonalPrivateRunFlowTests._display_headers

    def setUp(self):
        fixtures.PersonalPrivateRunFlowTests.setUp(self)
        if isinstance(self, TestCase):
            # The unit host owns its rollback transaction; committed probes do not.
            for path in ("apps.agents.task_execution.close_old_connections", "apps.agents.runtime_services.close_old_connections"):
                patcher = mock.patch(path)
                patcher.start()
                self.addCleanup(patcher.stop)

    def authenticate_durable_caller(self):
        self.authenticate_private_caller()

    def durable_payload(self, response):
        return response.json()

    def assert_durable_database(self):
        self.assertEqual(connection.vendor, "sqlite")


class PersonalDurableTaskTests(PersonalDurableFixture, DurableTaskGuards, TestCase):
    def test_expiry_finishes_nonfinancial_receipt_once(self):
        task = self.create_task()
        invocation = AgentRuntimeInvocation.objects.get(display_run=task.run, status="pending")
        receipt = PersonalInvocationUsage.objects.get(invocation=invocation)
        self.assertIsNone(receipt.finished_at)
        AgentExecutionTask.objects.filter(pk=task.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        worker.sweep()
        invocation.refresh_from_db()
        receipt.refresh_from_db()
        self.assertEqual(invocation.status, "failed")
        self.assertIsNotNone(receipt.finished_at)
        completed = receipt.finished_at
        latency = receipt.latency_ms
        self.assertEqual(worker.sweep(), 0)
        receipt.refresh_from_db()
        self.assertEqual(receipt.finished_at, completed)
        self.assertEqual(receipt.latency_ms, latency)
        self.assertEqual(PersonalInvocationUsage.objects.filter(invocation=invocation).count(), 1)

    def test_committed_result_and_nonfinancial_receipt_survive_worker_death(self):
        task = self.create_task()
        worker.claim()
        invocation = AgentRuntimeInvocation.objects.get(display_run=task.run)
        completed = timezone.now()
        AgentExecutionTask.objects.filter(pk=task.pk).update(status="completed", result_json={"ok": True})
        AgentRuntimeInvocation.objects.filter(pk=invocation.pk).update(status="success")
        PersonalInvocationUsage.objects.filter(invocation=invocation).update(finished_at=completed)
        AgentTaskExecution.objects.filter(task=task).update(lease_expires_at=completed - timedelta(seconds=1))
        worker.sweep()
        task.refresh_from_db()
        self.assertEqual(task.status, "completed")
        self.assertEqual(task.result_json, {"ok": True})
        self.assertEqual(task.run.status, "completed")
        self.assertEqual(task.execution.state, "finished")
        self.assertEqual(worker.sweep(), 0)
        self.assertEqual(PersonalInvocationUsage.objects.get(invocation=invocation).finished_at, completed)

    def test_admission_requires_owner_csrf_and_fixed_project(self):
        path = f"/api/v1/agents/{self.agent.pk}/private-runs/"
        body = {"content": "not authorized"}
        self.assertEqual(APIClient().post(path, body, format="json").status_code, 401)
        self.assertEqual(self.client.post(path, body, format="json").status_code, 403)
        self.assertEqual(self.client.post(path, body, format="json",
            **{**self._headers(), "HTTP_X_NEXUS_PROJECT": "foreign"}).status_code, 403)
        self.assertFalse(AgentExecutionTask.objects.exists())

    def test_display_token_renewal_keeps_other_tab_but_never_authorizes_non_owner(self):
        task = self.create_task()
        first = self._display_headers(str(task.run_id))
        second = self._display_headers(str(task.run_id))
        path = f"/api/v1/agent-runs/{task.run_id}/display/"
        for headers in (first, second):
            response = self.client.get(path, **headers)
            self.assertEqual(response.status_code, 200, response.content)
        other = get_user_model().objects.create_user(username="foreign-durable-owner")
        AccountProfile.objects.create(user=other, tenant_id=str(self.tenant.pk), project_id=str(self.project_a.pk), status="active")
        peer = APIClient()
        peer.credentials(HTTP_AUTHORIZATION="Bearer " + Token.objects.create(user=other).key)
        self.assertEqual(peer.get(path, **first).status_code, 401)


class PersonalTaskStreamTests(TaskStreamGuards, TestCase):
    pass
