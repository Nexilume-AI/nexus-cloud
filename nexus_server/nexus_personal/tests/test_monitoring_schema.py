"""Real operational schema and incident constraints, without report tables."""
from decimal import Decimal
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, transaction, IntegrityError
from django.test import TestCase, RequestFactory
from django.utils import timezone
from apps.metrics import models, serializers
from nexus_personal.services import provision_owner


class PersonalMonitoringSchemaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.installation = provision_owner(email="schema-owner@example.test", password="fixture-schema-owner-6928!")
        cls.rule = models.AlertRule.objects.create(tenant=cls.installation.tenant,
            project=cls.installation.project, created_by=cls.installation.owner,
            metric="system.quality.error_rate", threshold="10%", threshold_value=10, threshold_unit="%")

    def event(self, **options):
        return models.AlertEvent.objects.create(tenant=self.installation.tenant,
            rule=self.rule, metric=self.rule.metric, threshold="10%", threshold_value=10,
            value=Decimal("25"), triggered_at=timezone.now(), **options)

    def test_only_five_operational_models_and_real_tables_are_installed(self):
        self.assertEqual({model.__name__ for model in apps.get_app_config("metrics").get_models()},
            {"MetricSnapshot", "AlertRule", "AlertEvent", "AlertNotification", "MonitoringHeartbeat"})
        tables = connection.introspection.table_names()
        self.assertEqual({table for table in tables if table.startswith("metrics_")},
            {"metrics_metricsnapshot", "metrics_alertrule", "metrics_alertevent",
             "metrics_alertnotification", "metrics_monitoringheartbeat"})
        for name in ("ReportSchedule", "ReportDelivery"):
            self.assertFalse(hasattr(models, name))
        for name in ("ReportScheduleSerializer", "ReportScheduleCreateSerializer", "ReportDeliverySerializer"):
            with self.assertRaises(ImproperlyConfigured):
                getattr(serializers, name)

    def test_incident_uniqueness_and_resolved_history_are_real_constraints(self):
        first = self.event()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.event()
        self.event(is_test=True)
        first.status = "resolved"
        first.resolved_at = timezone.now()
        first.save(update_fields=["status", "resolved_at"])
        second = self.event()
        self.assertNotEqual(second.pk, first.pk)
        self.assertEqual(models.AlertEvent.objects.filter(rule=self.rule).count(), 3)

    def test_notification_identity_and_collector_lease_are_persisted(self):
        event = self.event()
        row = models.AlertNotification.objects.create(tenant=self.installation.tenant, event=event,
            target="schema-owner@example.test", phase="firing", delivery_key="unique-delivery")
        with self.assertRaises(IntegrityError), transaction.atomic():
            models.AlertNotification.objects.create(tenant=self.installation.tenant, event=event,
                target=row.target, phase="firing", delivery_key=row.delivery_key)
        heartbeat = models.MonitoringHeartbeat.objects.create(tenant=self.installation.tenant, component="collector",
            lease_id="fixture-lease", lease_until=timezone.now(), last_started_at=timezone.now())
        heartbeat.refresh_from_db()
        self.assertEqual(heartbeat.lease_id, "fixture-lease")
        with self.assertRaises(IntegrityError), transaction.atomic():
            models.MonitoringHeartbeat.objects.create(tenant=self.installation.tenant, component="collector")
        row.attempts, row.delivery_status = 1, "failed"
        row.save(update_fields=["attempts", "delivery_status"])
        self.assertEqual(models.AlertNotification.objects.get(pk=row.pk).attempts, 1)

    def test_shared_operational_serializers_do_not_require_report_models(self):
        request = RequestFactory().get("/api/v1/alerts/")
        request.user = self.installation.owner
        rule = serializers.AlertRuleSerializer(self.rule, context={"request": request}).data
        self.assertEqual(rule["metric"], "system.quality.error_rate")
        event = serializers.AlertEventSerializer(self.event(), context={"request": request}).data
        self.assertEqual(Decimal(event["value"]), Decimal("25"))
        snapshot = models.MetricSnapshot.objects.create(tenant=self.installation.tenant,
            resource_type="system", metrics_json={"usage": {"requests": 3}})
        self.assertEqual(serializers.MetricSnapshotSerializer(snapshot).data["metrics_json"]["usage"]["requests"], 3)
