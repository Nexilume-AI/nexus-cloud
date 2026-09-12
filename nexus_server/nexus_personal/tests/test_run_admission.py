"""Database-backed personal Run admission; not runner or billing emulation."""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import exceptions

from apps.agents.models import Agent, AgentDisplayRun
from apps.agents.task_execution import task_deadline
from apps.common.resource_limits import capability_state, enforce_capability, reserve_capability, release_capability_reservation
from apps.tenancy.models import Tenant
from nexus_personal.models import PersonalRunReservation
from nexus_personal.resource_limits import PersonalCapacityExceeded
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


class PersonalRunAdmissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="admission@example.test", password=PASSWORD)
        cls.agent = Agent.objects.create(tenant=cls.row.tenant, project=cls.row.project,
            created_by=cls.row.owner, name="Run admission")

    def limits(self, **changes):
        return override_settings(NEXUS_PERSONAL_AGENT_LIMITS={**settings.NEXUS_PERSONAL_AGENT_LIMITS, **changes})

    def reserve(self, run_id=None, **kwargs):
        return reserve_capability(tenant=self.row.tenant, code="agents.concurrent_runs",
            idempotency_key=f"agent-run:{run_id or uuid4()}", **kwargs)

    def release(self, run_id):
        release_capability_reservation(tenant=self.row.tenant, code="agents.concurrent_runs",
            idempotency_key=f"agent-run:{run_id}")

    def state(self):
        return capability_state(tenant=self.row.tenant, code="agents.concurrent_runs")

    def create_run(self, **kwargs):
        return AgentDisplayRun.objects.create(tenant=self.row.tenant, agent=self.agent,
            consumer_tenant=self.row.tenant, consumer_project=self.row.project,
            run_kind="invocation", status="running", **kwargs)

    def test_reservation_replay_is_one_slot_without_extending_deadline(self):
        with self.limits(**{"agents.concurrent_runs": 1}):
            first = self.reserve(ttl_seconds=100)
            replay = self.reserve(first.run_id, ttl_seconds=300)
            self.assertEqual(first.expires_at, replay.expires_at)
            self.assertEqual(self.state()["used"], 1)
            with self.assertRaises(PersonalCapacityExceeded):
                self.reserve()
            self.assertEqual(PersonalRunReservation.objects.count(), 1)

    def test_committed_run_takes_over_slot_and_completion_releases_it(self):
        first = self.reserve()
        run = self.create_run(id=first.run_id)
        self.assertEqual(self.state()["used"], 1)
        self.release(first.run_id)
        self.release(first.run_id)
        self.assertEqual(self.state()["used"], 1)
        for status in ("completed", "failed"):
            run.status = status
            run.save(update_fields=["status"])
            self.assertEqual(self.state()["used"], 0)
        run.status = "running"
        run.save(update_fields=["status"])
        self.assertEqual(self.state()["used"], 1)
        with self.limits(**{"agents.concurrent_runs": 1}), self.assertRaises(PersonalCapacityExceeded):
            enforce_capability(tenant=self.row.tenant, code="agents.concurrent_runs")

    def test_delayed_release_of_finished_run_does_not_leave_phantom_slot(self):
        reservation = self.reserve()
        run = self.create_run(id=reservation.run_id)
        run.status = "completed"
        run.save(update_fields=["status"])
        self.assertEqual(self.state()["used"], 0)

    def test_expiry_recovers_capacity_but_cannot_resurrect_old_request(self):
        first = self.reserve()
        PersonalRunReservation.objects.filter(pk=first.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.state()["used"], 0)
        with self.assertRaises(exceptions.ValidationError):
            self.reserve(first.pk)
        self.assertIsNotNone(self.reserve())
        self.release(first.pk)

    def test_outer_transaction_failure_rolls_back_admission(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                reservation = self.reserve()
                self.create_run(id=reservation.pk)
                raise RuntimeError("Later registration step failed")
        self.assertFalse(PersonalRunReservation.objects.exists())
        self.assertFalse(AgentDisplayRun.objects.exists())

    def test_cleanup_survives_lowered_limit_and_disabled_owner(self):
        first = self.reserve()
        self.row.owner.is_active = False
        self.row.owner.save(update_fields=["is_active"])
        with override_settings(NEXUS_PERSONAL_AGENT_LIMITS={}):
            self.release(first.pk)
        first.refresh_from_db()
        self.assertIsNotNone(first.released_at)

    def test_reject_foreign_context_existing_run_and_invalid_requests(self):
        foreign = Tenant.objects.create(name="Foreign", slug="foreign-run-admission")
        for method in (reserve_capability, release_capability_reservation):
            with self.assertRaises(exceptions.NotFound):
                method(tenant=foreign, code="agents.concurrent_runs", idempotency_key=f"agent-run:{uuid4()}")
        run = self.create_run()
        with self.assertRaises(exceptions.ValidationError):
            self.reserve(run.pk)
        for values in ({"amount": 0}, {"amount": 2}, {"amount": True}, {"amount": 1.0},
                       {"ttl_seconds": 0}, {"ttl_seconds": 301}, {"ttl_seconds": True}):
            with self.subTest(values=values), self.assertRaises(exceptions.ValidationError):
                self.reserve(**values)
        for key in ("agent-run:bad", "wrong", None):
            with self.assertRaises(exceptions.ValidationError):
                reserve_capability(tenant=self.row.tenant, code="agents.concurrent_runs", idempotency_key=key)

    def test_duration_limit_reaches_real_task_deadline_and_unknown_usage_fails_closed(self):
        with self.limits(**{"agents.run_minutes_per_run": "1.5"}):
            before = timezone.now()
            deadline = task_deadline(self.row.tenant)
            self.assertGreaterEqual(deadline, before + timedelta(seconds=90))
            self.assertLess(deadline, before + timedelta(seconds=92))
        for invalid in (0, None, "NaN", 1.5, 1441):
            with self.limits(**{"agents.run_minutes_per_run": invalid}), self.assertRaises(ImproperlyConfigured):
                task_deadline(self.row.tenant)
        with self.assertRaises(ImproperlyConfigured):
            capability_state(tenant=self.row.tenant, code="agents.unknown")
        with self.limits(**{"agents.concurrent_runs": 0}):
            self.assertEqual(self.state()["remaining"], Decimal(0))
            with self.assertRaises(PersonalCapacityExceeded):
                self.reserve()
