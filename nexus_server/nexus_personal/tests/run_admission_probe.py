"""Committed two-thread admission against the personal file SQLite host."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

from django.db import connections
from django.test import TransactionTestCase, override_settings
from apps.common.resource_limits import reserve_capability, capability_state
from nexus_personal.models import PersonalRunReservation
from nexus_personal.resource_limits import PersonalCapacityExceeded
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


@override_settings(NEXUS_PERSONAL_AGENT_LIMITS={"agents.concurrent_runs": 1})
class PersonalRunAdmissionRaceTests(TransactionTestCase):
    def setUp(self):
        self.row = provision_owner(email="race@example.test", password=PASSWORD)

    def race(self, identities):
        barrier = Barrier(2)

        def submit(identity):
            try:
                barrier.wait(timeout=10)
                row = reserve_capability(tenant=self.row.tenant_id, code="agents.concurrent_runs",
                    idempotency_key=f"agent-run:{identity}")
                return str(row.pk)
            except PersonalCapacityExceeded:
                return "capacity-denied"
            finally:
                # Worker-owned connections must close before the test database
                # is flushed/deleted, including on an unexpected exception.
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, identities))
        self.assertEqual(PersonalRunReservation.objects.count(), 1)
        self.assertEqual(capability_state(tenant=self.row.tenant_id, code="agents.concurrent_runs")["used"], 1)
        return results

    def test_different_runs_cannot_both_acquire_final_slot(self):
        results = self.race([uuid4(), uuid4()])
        self.assertEqual(results.count("capacity-denied"), 1)

    def test_same_request_concurrent_replay_shares_one_reservation(self):
        identity = uuid4()
        self.assertEqual(self.race([identity, identity]), [str(identity), str(identity)])


@override_settings(NEXUS_PERSONAL_AGENT_LIMITS={"agents.concurrent_runs": 5,
    "agents.run_minutes_per_run": 1, "agents.runs_per_30_days": 1, "agents.run_minutes_per_30_days": 1})
class PersonalInvocationRaceTests(TransactionTestCase):
    def setUp(self):
        from apps.agents.models import Agent, AgentRuntimeDeployment, AgentRuntimeImage
        self.row = provision_owner(email="invocation-race@example.test", password=PASSWORD)
        self.agent = Agent.objects.create(tenant=self.row.tenant, project=self.row.project,
            created_by=self.row.owner, name="Concurrent lifecycle", status="active")
        image = AgentRuntimeImage.objects.create(agent=self.agent, tenant=self.row.tenant,
            project=self.row.project, image_ref="concurrency:unit")
        self.runtime = AgentRuntimeDeployment.objects.create(agent=self.agent, tenant=self.row.tenant,
            project=self.row.project, image=image, status="active")

    def request(self):
        return SimpleNamespace(user=self.row.owner, tenant_id=str(self.row.tenant_id),
            project_id=str(self.row.project_id), META={}, headers={}, query_params={},
            build_absolute_uri=lambda path: "https://personal.test" + path)

    def context(self):
        from apps.agents.runtime_services import create_invocation_display_context
        return create_invocation_display_context(runtime=self.runtime, request=self.request(), tool_name="echo")[0]

    def race(self, runs):
        from apps.common.invocation_lifecycle import begin_runtime_invocation
        barrier = Barrier(2)

        def submit(run):
            try:
                barrier.wait(timeout=10)
                row = begin_runtime_invocation(request=self.request(), tenant=self.row.tenant, agent=self.agent,
                    runtime=self.runtime, api_key=None, tool_name="echo", display_run=run, turn_index=1)
                return str(row.pk)
            except PersonalCapacityExceeded:
                return "capacity-denied"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(submit, runs))

    def test_two_runs_cannot_both_consume_final_period_allowance(self):
        from apps.agents.models import AgentDisplayRun, AgentRuntimeInvocation
        runs = [self.context(), self.context()]
        results = self.race(runs)
        self.assertEqual(results.count("capacity-denied"), 1)
        self.assertEqual(AgentRuntimeInvocation.objects.count(), 1)
        self.assertEqual(AgentDisplayRun.objects.filter(status="failed").count(), 1)
        self.assertEqual(capability_state(tenant=self.row.tenant, code="agents.concurrent_runs")["used"], 1)

    def test_concurrent_same_turn_creates_one_invocation_and_receipt(self):
        from nexus_personal.models import PersonalInvocationUsage
        run = self.context()
        results = self.race([run, run])
        self.assertEqual(results[0], results[1])
        self.assertNotEqual(results[0], "capacity-denied")
        self.assertEqual(PersonalInvocationUsage.objects.count(), 1)
