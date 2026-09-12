"""Shared lifecycle unit contracts. Fake runner is never live acceptance."""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.core.cache import cache
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import APIException

from apps.agents import docker_lifecycle as lifecycle
from apps.agents.models import AgentRuntimeDeployment, AgentLog
from apps.agents.runtime_runner import FakeAgentRuntimeRunner
from apps.agents.runtime_services import perform_runtime_deploy, perform_runtime_stop, AgentRuntimeError, AgentRuntimeOperationConflict



class DockerLifecycleGuards:
    def test_failure_status_survives_transaction_rollback(self):
        with patch.object(FakeAgentRuntimeRunner, "start", side_effect=APIException("private upstream detail")):
            response = self.deploy()
        self.assertEqual(response.status_code, 400, response.content)
        runtime = self.runtime()
        self.assertEqual(runtime.status, "failed")
        self.assertEqual(runtime.agent_deployment.status, "failed")
        self.assertNotIn("private upstream", runtime.last_error)
        self.assertTrue(AgentLog.objects.filter(agent=self.agent, level="error").exists())
        self.assertNotIn("lease_token", runtime.docker_lifecycle)

    def test_redeploy_retires_exact_previous_container(self):
        self.assertEqual(self.deploy().status_code, 201)
        previous = self.runtime().container_id
        with patch.object(FakeAgentRuntimeRunner, "stop") as stop:
            self.assertEqual(self.deploy().status_code, 201)
        self.assertNotEqual(previous, self.runtime().container_id)
        self.assertEqual(stop.call_args.kwargs["deployment"].container_id, previous)
        self.assertEqual(self.runtime().docker_lifecycle["retiring"], [])

    def test_failed_replacement_preserves_previous_healthy_container(self):
        self.assertEqual(self.deploy().status_code, 201)
        previous = self.runtime().container_id
        with patch.object(FakeAgentRuntimeRunner, "start", side_effect=APIException("cannot start")):
            self.assertEqual(self.deploy().status_code, 400)
        current = self.runtime()
        self.assertEqual(current.container_id, previous)
        self.assertEqual(current.status, "active")
        self.assertIn("previous deployment remains active", current.last_error)

    def test_retirement_failure_remains_for_retry(self):
        self.assertEqual(self.deploy().status_code, 201)
        previous = self.runtime().container_id
        with patch.object(FakeAgentRuntimeRunner, "stop", side_effect=APIException("stop failed")):
            self.assertEqual(self.deploy().status_code, 201)
        self.assertEqual(self.runtime().docker_lifecycle["retiring"][0]["container_id"], previous)
        lifecycle.retire(runtime_id=self.runtime().id)
        self.assertEqual(self.runtime().docker_lifecycle["retiring"], [])

    def test_recovery_keeps_same_runtime_and_does_not_replay_invocations(self):
        self.assertEqual(self.deploy().status_code, 201)
        pk = self.runtime().pk
        with patch.object(FakeAgentRuntimeRunner, "health_check", return_value=False), \
             patch.object(FakeAgentRuntimeRunner, "call_mcp", wraps=FakeAgentRuntimeRunner().call_mcp) as mcp:
            self.assertEqual(lifecycle.reconcile(), 1)
        self.assertEqual(self.runtime().pk, pk)
        self.assertEqual(self.runtime().status, "active")
        for call in mcp.call_args_list:
            self.assertNotIn(b'"tools/call"', call.kwargs.get("body", b""))

    def test_explicit_stop_is_not_resurrected(self):
        self.assertEqual(self.deploy().status_code, 201)
        perform_runtime_stop(runtime_id=self.runtime().id)
        with patch.object(FakeAgentRuntimeRunner, "start") as start:
            lifecycle.reconcile()
        start.assert_not_called()
        self.assertEqual(self.runtime().status, "stopped")

    def test_stop_failure_preserves_stop_intent_and_retries(self):
        self.assertEqual(self.deploy().status_code, 201)
        with patch.object(FakeAgentRuntimeRunner, "stop", side_effect=APIException("stop failed")):
            with self.assertRaises(AgentRuntimeError):
                perform_runtime_stop(runtime_id=self.runtime().id)
        self.assertEqual(self.runtime().docker_lifecycle["desired"], "stopped")
        lifecycle.reconcile()
        self.assertEqual(self.runtime().status, "stopped")

    def test_duplicate_deploy_job_does_not_start_another_container(self):
        self.assertEqual(self.deploy().status_code, 201)
        with patch.object(FakeAgentRuntimeRunner, "start") as start:
            perform_runtime_deploy(runtime_id=self.runtime().id)
        start.assert_not_called()

    def test_live_operation_lease_prevents_concurrent_claim(self):
        self.assertEqual(self.deploy().status_code, 201)
        runtime = self.runtime()
        runtime.docker_lifecycle.update(lease_token="worker", lease_until=(timezone.now() + timedelta(minutes=5)).isoformat())
        runtime.save()
        with self.assertRaises(AgentRuntimeOperationConflict):
            lifecycle._claim(runtime.id, desired="running", recovery=True)

    @override_settings(NEXUS_AGENT_RUNTIME_LEASE_SECONDS=45)
    def test_operation_claim_uses_short_recoverable_lease(self):
        self.assertEqual(self.deploy().status_code, 201)
        runtime = self.runtime()
        runtime.status = "failed"
        runtime.docker_lifecycle.pop("lease_token", None)
        runtime.docker_lifecycle.pop("lease_until", None)
        runtime.save()
        claimed = lifecycle._claim(runtime.id, desired="running", recovery=True)
        lease_until = parse_datetime(claimed.docker_lifecycle["lease_until"])
        remaining = (lease_until - timezone.now()).total_seconds()
        self.assertGreater(remaining, 35)
        self.assertLessEqual(remaining, 46)

    def test_distributed_reconcile_lock_prevents_overlapping_sweeps(self):
        cache.set(lifecycle.RECONCILE_LOCK_KEY, "another-worker", timeout=60)
        with patch.object(FakeAgentRuntimeRunner, "health_check") as health:
            self.assertEqual(lifecycle.reconcile(), 0)
        health.assert_not_called()

    def test_backoff_and_foreign_host_are_not_claimed(self):
        self.assertEqual(self.deploy().status_code, 201)
        runtime = self.runtime()
        runtime.docker_lifecycle.update(retry_at=(timezone.now() + timedelta(minutes=5)).isoformat())
        runtime.save()
        with patch.object(FakeAgentRuntimeRunner, "start") as start:
            lifecycle.reconcile()
        start.assert_not_called()
        runtime.docker_lifecycle.update(host_id="another-worker", retry_at="")
        runtime.save()
        self.assertEqual(lifecycle.reconcile(), 0)

    def test_broker_failure_retains_stop_intent_and_reconciles_job(self):
        from apps.jobs.models import Job
        from kombu.exceptions import OperationalError
        self.assertEqual(self.deploy().status_code, 201)
        with patch("apps.agents.tasks.stop_runtime_job.delay", side_effect=OperationalError("queue offline")):
            response = self.client.post(f"/api/v1/agents/{self.agent.id}/runtime/deployments/stop/",
                {"env": "prod"}, format="json", HTTP_X_NEXUS_TENANT=str(self.tenant.id))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.runtime().docker_lifecycle["desired"], "stopped")
        self.assertEqual(self.runtime().effective_status(), "stopped")
        with patch.object(FakeAgentRuntimeRunner, "start") as start:
            lifecycle.reconcile()
        start.assert_not_called()
        self.assertEqual(self.runtime().status, "stopped")
        self.assertEqual(Job.objects.get(job_type="agents.runtime.stop", resource_id=str(self.runtime().id)).status, "succeeded")

    def test_completed_stop_job_cannot_stop_a_new_deployment(self):
        from apps.agents.tasks import stop_runtime_job
        from apps.jobs.models import Job
        self.assertEqual(self.deploy().status_code, 201)
        response = self.client.post(f"/api/v1/agents/{self.agent.id}/runtime/deployments/stop/",
            {"env": "prod"}, format="json", HTTP_X_NEXUS_TENANT=str(self.tenant.id))
        self.assertLess(response.status_code, 300, response.content)
        job = Job.objects.get(job_type="agents.runtime.stop", resource_id=str(self.runtime().id))
        old_generation = job.input_json["docker_generation"]
        self.assertEqual(self.deploy().status_code, 201)
        with patch.object(FakeAgentRuntimeRunner, "stop") as stop:
            stop_runtime_job(str(job.id), str(self.runtime().id))
        stop.assert_not_called()
        with self.assertRaises(AgentRuntimeOperationConflict):
            perform_runtime_stop(runtime_id=self.runtime().id, expected_generation=old_generation)
        self.assertEqual(self.runtime().status, "active")

    def test_exhausted_recovery_finishes_abandoned_job(self):
        from apps.jobs.models import Job
        self.assertEqual(self.deploy().status_code, 201)
        runtime = self.runtime()
        runtime.status = "failed"
        runtime.docker_lifecycle.update(attention_required=True, attempts=5)
        runtime.save()
        job = Job.objects.create(tenant=self.tenant, job_type="agents.runtime.deploy",
                                 resource_type="agent_runtime_deployment", resource_id=str(runtime.id))
        lifecycle.reconcile()
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")

    def test_transient_attention_state_keeps_half_open_recovery(self):
        self.assertEqual(self.deploy().status_code, 201)
        runtime = self.runtime()
        runtime.status = "failed"
        runtime.docker_lifecycle.update(
            attention_required=True,
            permanent_failure=False,
            attempts=8,
            retry_at=(timezone.now() - timedelta(seconds=1)).isoformat(),
        )
        runtime.save()

        with patch.object(FakeAgentRuntimeRunner, "start", wraps=FakeAgentRuntimeRunner().start) as start:
            self.assertEqual(lifecycle.reconcile(), 1)

        start.assert_called_once()
        self.assertEqual(self.runtime().status, "active")

    def test_permanent_policy_failure_is_not_automatically_retried(self):
        self.assertEqual(self.deploy().status_code, 201)
        runtime = self.runtime()
        runtime.status = "failed"
        runtime.docker_lifecycle.update(
            attention_required=True,
            permanent_failure=True,
            attempts=1,
            retry_at=(timezone.now() - timedelta(seconds=1)).isoformat(),
        )
        runtime.save()

        with patch.object(FakeAgentRuntimeRunner, "start") as start:
            self.assertEqual(lifecycle.reconcile(), 0)

        start.assert_not_called()

    def test_disabled_agent_is_not_claimed_after_candidate_selection(self):
        self.assertEqual(self.deploy().status_code, 201)
        self.agent.status = "disabled"
        self.agent.save(update_fields=["status"])
        with self.assertRaises(AgentRuntimeOperationConflict):
            lifecycle._claim(self.runtime().id, desired="running", recovery=True)

    def test_stop_does_not_revalidate_resources_or_require_current_image_admission(self):
        self.assertEqual(self.deploy().status_code, 201)
        runtime = self.runtime()
        runtime.docker_lifecycle.pop("resources")
        runtime.save()
        with patch("apps.agents.docker_lifecycle.resource_limits", side_effect=APIException("invalid current settings")):
            perform_runtime_stop(runtime_id=runtime.id)
        self.assertEqual(self.runtime().status, "stopped")

    def test_disable_during_start_is_not_undone_by_completion(self):
        original = FakeAgentRuntimeRunner.start
        def concurrent_disable(runner, **kwargs):
            type(self.agent).objects.filter(pk=self.agent.pk).update(status="disabled")
            return original(runner, **kwargs)
        with patch.object(FakeAgentRuntimeRunner, "start", concurrent_disable):
            self.assertEqual(self.deploy().status_code, 201)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.status, "disabled")
