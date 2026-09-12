"""Actual DB leases, incidents, SMTP protocol and bounded scoped retention."""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.agents.models import Agent
from apps.audit.models import AuditLog
from apps.metrics.models import AlertRule, AlertEvent, AlertNotification, MetricSnapshot, MonitoringHeartbeat
from apps.metrics import worker_leases
from apps.tenancy.models import Tenant, Project
from nexus_personal import monitoring_workers as workers
from nexus_personal.services import provision_owner
from .monitoring_smtp_fixture import SMTPFixture


@override_settings(ROOT_URLCONF="nexus_personal.urls")
class PersonalMonitoringWorkerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.installation = provision_owner(email="worker-owner@example.test", password="fixture-monitor-worker-32894!")
        cls.owner, cls.tenant, cls.project = cls.installation.owner, cls.installation.tenant, cls.installation.project
        cls.agent = Agent.objects.create(tenant=cls.tenant, project=cls.project, name="Observed")
        cls.foreign_tenant = Tenant.objects.create(name="Foreign")
        cls.foreign_project = Project.objects.create(tenant=cls.foreign_tenant, name="Foreign")
        cls.other_project = Project.objects.create(tenant=cls.tenant, name="Not personal context")
        cls.other_owner = get_user_model().objects.create_user(username="not-personal-owner")

    def setUp(self):
        self.client = APIClient()
        self.client.force_login(self.owner)

    def rule(self, **kwargs):
        return AlertRule.objects.create(**{"tenant": self.tenant, "project": self.project, "created_by": self.owner,
            "metric": "system.counts.agents", "threshold": "1", "threshold_value": Decimal(1), **kwargs})

    def event(self, rule=None):
        rule = rule or self.rule()
        return AlertEvent.objects.create(tenant=rule.tenant, rule=rule, metric=rule.metric,
            value=1, threshold="1", threshold_value=1, triggered_at=timezone.now())

    def test_collect_real_snapshot_and_project_audit_without_private_fields(self):
        self.assertEqual(workers.collect_snapshots(), 1)
        snapshot = MetricSnapshot.objects.get(tenant=self.tenant)
        self.assertEqual(snapshot.metrics_json["counts"]["agents"], 1)
        self.assertEqual(snapshot.metrics_json["window"]["usage"]["requests"], 0)
        self.assertFalse(snapshot.metrics_json["window"]["has_samples"])
        self.assertNotIn("cost", str(snapshot.metrics_json))
        heartbeat = MonitoringHeartbeat.objects.get(tenant=self.tenant, component="collector")
        self.assertIsNotNone(heartbeat.last_success_at)
        self.assertEqual(heartbeat.lease_id, "")
        audit = AuditLog.objects.get(action="metrics.snapshot.capture", resource_id=str(snapshot.pk))
        self.assertEqual(audit.project_id, str(self.project.pk))
        self.assertIsNone(audit.actor_id)
        self.assertEqual(self.client.get(f"/api/v1/metrics/audit/{audit.pk}/").status_code, 200)

    def test_collector_failure_rolls_back_snapshot_without_fake_success(self):
        with patch.object(workers, "_audit", side_effect=RuntimeError("secret-driver-error")):
            self.assertEqual(workers.collect_snapshots(), 0)
        self.assertFalse(MetricSnapshot.objects.exists())
        row = MonitoringHeartbeat.objects.get(tenant=self.tenant, component="collector")
        self.assertIsNone(row.last_success_at)
        self.assertEqual(row.last_error_code, "COLLECTION_FAILED")

    def test_component_lease_exclusion_recovery_and_old_completion_fencing(self):
        now = timezone.now()
        first = worker_leases.claim_component(self.tenant, "collector", now)
        self.assertTrue(first)
        self.assertIsNone(worker_leases.claim_component(self.tenant, "collector", now))
        self.assertEqual(workers.collect_snapshots(), 0)
        second = worker_leases.claim_component(self.tenant, "collector", now + timedelta(minutes=6))
        self.assertNotEqual(first, second)
        worker_leases.finish_component(self.tenant, "collector", first)
        row = MonitoringHeartbeat.objects.get(tenant=self.tenant, component="collector")
        self.assertEqual(row.lease_id, second)
        self.assertIsNone(row.last_success_at)
        worker_leases.finish_component(self.tenant, "collector", second)
        row.refresh_from_db()
        self.assertEqual(row.lease_id, "")
        self.assertIsNotNone(row.last_success_at)

    def test_firing_resolution_cooldown_and_no_duplicate_open_incidents(self):
        rule, now = self.rule(), timezone.now()
        self.assertEqual(workers.evaluate_rules(now=now)["fired"], 1)
        self.assertEqual(workers.evaluate_rules(now=now)["fired"], 0)
        event = AlertEvent.objects.get(rule=rule)
        self.assertEqual(event.notifications.count(), 1)
        Agent.objects.filter(pk=self.agent.pk).update(status="deleted")
        self.assertEqual(workers.evaluate_rules(now=now + timedelta(seconds=1))["resolved"], 1)
        self.assertEqual(workers.evaluate_rules(now=now + timedelta(seconds=2))["resolved"], 0)
        event.refresh_from_db()
        self.assertEqual(event.status, "resolved")
        self.assertEqual(set(event.notifications.values_list("phase", flat=True)), {"firing", "resolved"})
        Agent.objects.filter(pk=self.agent.pk).update(status="active")
        self.assertEqual(workers.evaluate_rules(now=now + timedelta(seconds=3))["fired"], 0)
        self.assertEqual(workers.evaluate_rules(now=now + timedelta(seconds=301))["fired"], 1)
        self.assertEqual(AlertEvent.objects.filter(rule=rule, status="firing").count(), 1)
        self.assertEqual(AlertEvent.objects.filter(rule=rule).count(), 2)

    def test_paused_muted_foreign_project_and_other_owner_are_not_dispatched(self):
        self.rule(status="disabled")
        muted = self.rule(muted_until=timezone.now() + timedelta(hours=1))
        self.rule(tenant=self.foreign_tenant, project=self.foreign_project)
        self.rule(project=self.other_project)
        self.rule(created_by=self.other_owner)
        self.assertEqual(workers.evaluate_rules()["fired"], 0)
        self.assertFalse(AlertEvent.objects.exists())
        muted.refresh_from_db()
        self.assertIsNotNone(muted.last_evaluated_at)
        self.assertFalse(MonitoringHeartbeat.objects.filter(tenant=self.foreign_tenant).exists())

    def test_no_data_and_unavailable_metrics_have_only_safe_error_codes(self):
        rule = self.rule(metric="system.quality.p95_latency_ms", resource_type="agent", resource_id=str(self.agent.pk))
        self.assertEqual(workers.evaluate_rules()["failed"], 1)
        rule.refresh_from_db()
        self.assertEqual(rule.last_error_code, "NO_DATA")
        with patch.object(workers, "resolve_rule", side_effect=RuntimeError("secret-token-and-sql")):
            self.assertEqual(workers.evaluate_rules()["failed"], 1)
        rule.refresh_from_db()
        self.assertEqual(rule.last_error_code, "METRIC_UNAVAILABLE")
        self.assertFalse(AlertNotification.objects.exists())
        response = self.client.get("/api/v1/metrics/system/")
        self.assertNotIn("secret-token", response.content.decode())
        self.assertEqual(response.data["summary"]["rule_evaluation_errors"], 1)

    def test_atomic_incident_and_notification_on_audit_failure(self):
        rule = self.rule()
        with patch.object(workers, "_audit", side_effect=RuntimeError("audit failure")):
            self.assertEqual(workers.evaluate_rules()["failed"], 1)
        self.assertFalse(AlertEvent.objects.filter(rule=rule).exists())
        self.assertFalse(AlertNotification.objects.exists())
        self.assertEqual(workers.evaluate_rules()["fired"], 1)

    def test_outbox_idempotency_and_real_smtp_acceptance(self):
        event = self.event()
        first = workers.enqueue_alert(event)
        second = workers.enqueue_alert(event)
        self.assertEqual([row.pk for row in first], [row.pk for row in second])
        with SMTPFixture() as server, override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
                EMAIL_HOST="127.0.0.1", EMAIL_PORT=server.server_address[1], EMAIL_HOST_USER="", EMAIL_HOST_PASSWORD="",
                EMAIL_USE_TLS=False, EMAIL_USE_SSL=False, EMAIL_TIMEOUT=5):
            self.assertEqual(workers.deliver_notifications()["sent"], 1)
            self.assertEqual(workers.deliver_notifications()["sent"], 0)
            self.assertEqual(len(server.messages), 1)
            self.assertIn(f"Message-ID: <{first[0].pk}@nexus-notifications>".encode(), server.messages[0])
        first[0].refresh_from_db()
        self.assertEqual(first[0].delivery_status, "sent")
        self.assertEqual(first[0].attempts, 1)
        self.assertIsNotNone(first[0].sent_at)
        self.assertTrue(AuditLog.objects.filter(action="metrics.alert_notification.send", project_id=str(self.project.pk)).exists())

    @override_settings(NEXUS_MONITOR_DELIVERY_MAX_ATTEMPTS=2)
    def test_real_smtp_rejection_backoff_then_terminal_failure(self):
        row = workers.enqueue_alert(self.event())[0]
        now = timezone.now()
        with SMTPFixture() as server, override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
                EMAIL_HOST="127.0.0.1", EMAIL_PORT=server.server_address[1], EMAIL_HOST_USER="", EMAIL_HOST_PASSWORD="",
                EMAIL_USE_TLS=False, EMAIL_USE_SSL=False, EMAIL_TIMEOUT=5):
            server.reject = True
            self.assertEqual(workers.deliver_notifications(now=now)["retrying"], 1)
            self.assertEqual(workers.deliver_notifications(now=now), {"sent": 0, "retrying": 0, "failed": 0})
            row.refresh_from_db()
            self.assertEqual(row.next_attempt_at, now + timedelta(seconds=30))
            self.assertEqual(workers.deliver_notifications(now=row.next_attempt_at)["failed"], 1)
            self.assertEqual(server.messages, [])
        row.refresh_from_db()
        self.assertEqual(row.attempts, 2)
        self.assertEqual(row.delivery_status, "failed")
        self.assertEqual(row.error_message, "EMAIL_TRANSPORT_FAILED")
        self.assertNotIn(self.owner.email, row.error_message)

    def test_delivery_rechecks_disabled_rule_and_cannot_claim_foreign_outbox(self):
        event = self.event()
        row = workers.enqueue_alert(event)[0]
        AlertRule.objects.filter(pk=event.rule_id).update(status="disabled")
        foreign_rule = self.rule(project=self.other_project)
        foreign_event = self.event(foreign_rule)
        foreign = AlertNotification.objects.create(tenant=self.tenant, event=foreign_event, target="nobody@example.test", delivery_key=str(uuid4()))
        with patch.object(workers.EmailMessage, "send") as send:
            self.assertEqual(workers.deliver_notifications()["failed"], 1)
            send.assert_not_called()
        row.refresh_from_db()
        foreign.refresh_from_db()
        self.assertEqual(row.error_code, "PERMISSION_OR_RULE_REVOKED")
        self.assertEqual(foreign.delivery_status, "pending")
        self.assertEqual(foreign.attempts, 0)

    @override_settings(NEXUS_MONITOR_SMTP_CONFIGURED=False)
    def test_unconfigured_smtp_preserves_pending_without_claiming_or_sending(self):
        row = workers.enqueue_alert(self.event())[0]
        with patch.object(workers.EmailMessage, "send") as send:
            self.assertEqual(workers.deliver_notifications(), {"sent": 0, "retrying": 0, "failed": 0})
            send.assert_not_called()
        row.refresh_from_db()
        self.assertEqual(row.delivery_status, "pending")
        self.assertEqual(row.attempts, 0)
        heartbeat = MonitoringHeartbeat.objects.get(tenant=self.tenant, component="delivery")
        self.assertIsNone(heartbeat.last_success_at)
        self.assertEqual(heartbeat.last_error_code, "EMAIL_NOT_CONFIGURED")
        response = self.client.get("/api/v1/metrics/system/")
        self.assertEqual(response.data["monitoring"]["state"], "unknown")
        self.assertIn("Configure SMTP", response.data["monitoring"]["delivery_notice"])

    def test_late_smtp_result_cannot_overwrite_new_delivery_claim(self):
        row = workers.enqueue_alert(self.event())[0]
        def late_send(**kwargs):
            AlertNotification.objects.filter(pk=row.pk).update(lease_id="new-worker", lease_until=timezone.now() + timedelta(minutes=1))
            return 1
        with patch.object(workers.EmailMessage, "send", side_effect=late_send):
            self.assertEqual(workers.deliver_notifications(limit=1), {"sent": 0, "retrying": 0, "failed": 0})
        row.refresh_from_db()
        self.assertEqual(row.delivery_status, "pending")
        self.assertEqual(row.lease_id, "new-worker")
        self.assertFalse(AuditLog.objects.filter(action="metrics.alert_notification.send", resource_id=str(row.pk)).exists())

    def test_cleanup_is_bounded_scoped_and_preserves_incidents_audit_and_unsent(self):
        old = timezone.now() - timedelta(days=120)
        event = self.event()
        pending = workers.enqueue_alert(event)[0]
        sent = AlertNotification.objects.create(tenant=self.tenant, event=event, target="done@example.test",
            delivery_key=str(uuid4()), delivery_status="sent", sent_at=old)
        for _ in range(3):
            row = MetricSnapshot.objects.create(tenant=self.tenant, resource_type="system", metrics_json={"project_id": str(self.project.pk)})
            MetricSnapshot.objects.filter(pk=row.pk).update(captured_at=old)
        foreign = MetricSnapshot.objects.create(tenant=self.foreign_tenant, resource_type="system")
        MetricSnapshot.objects.filter(pk=foreign.pk).update(captured_at=old)
        unknown = MetricSnapshot.objects.create(tenant=self.tenant, resource_type="system")
        other = MetricSnapshot.objects.create(tenant=self.tenant, resource_type="system", metrics_json={"project_id": str(self.other_project.pk)})
        MetricSnapshot.objects.filter(pk__in=[unknown.pk, other.pk]).update(captured_at=old)
        preview = workers.cleanup_monitoring(batch_size=2)
        self.assertEqual(preview["snapshots"], 2)
        self.assertEqual(preview["notifications"], 1)
        self.assertEqual(MetricSnapshot.objects.count(), 6)
        workers.cleanup_monitoring(dry_run=False, batch_size=2)
        self.assertEqual(MetricSnapshot.objects.filter(tenant=self.tenant).count(), 3)
        self.assertTrue(MetricSnapshot.objects.filter(pk=foreign.pk).exists())
        self.assertEqual(MetricSnapshot.objects.filter(pk__in=[unknown.pk, other.pk]).count(), 2)
        self.assertFalse(AlertNotification.objects.filter(pk=sent.pk).exists())
        self.assertTrue(AlertNotification.objects.filter(pk=pending.pk).exists())
        self.assertTrue(AlertEvent.objects.filter(pk=event.pk).exists())

    def test_invalid_limits_and_unavailable_owner_do_not_run(self):
        for value in (0, -1, 1001, True, "1"):
            with self.assertRaises(ValidationError):
                workers.deliver_notifications(limit=value)
            with self.assertRaises(ValidationError):
                workers.cleanup_monitoring(batch_size=value)
        self.owner.is_active = False
        self.owner.save(update_fields=["is_active"])
        for operation in (workers.collect_snapshots, workers.evaluate_rules, workers.deliver_notifications, workers.cleanup_monitoring):
            with self.assertRaises(ImproperlyConfigured):
                operation()
        self.assertFalse(MonitoringHeartbeat.objects.exists())

    def test_composed_operational_tasks_reach_workers_and_reports_are_absent(self):
        from apps.metrics import operations, operational_tasks
        from nexus_personal.worker_composition import TASK_MODULES, BEAT_SCHEDULE
        self.assertIs(operations, workers)
        self.assertFalse(hasattr(operations, "schedule_reports"))
        self.assertFalse(hasattr(operations, "enqueue_report"))
        self.assertIn("apps.metrics.operational_tasks", TASK_MODULES)
        expected = {f"apps.metrics.operational_tasks.{name}" for name in
            ("collect_snapshots", "evaluate_rules", "deliver_notifications", "cleanup_monitoring")}
        self.assertTrue(expected <= {row["task"] for row in BEAT_SCHEDULE.values()})
        self.assertEqual(operational_tasks.collect_snapshots.run(), 1)
        self.assertEqual(operational_tasks.evaluate_rules.run()["failed"], 0)
        self.assertEqual(operational_tasks.deliver_notifications.run()["sent"], 0)
        self.assertTrue(operational_tasks.cleanup_monitoring.run()["dry_run"])
