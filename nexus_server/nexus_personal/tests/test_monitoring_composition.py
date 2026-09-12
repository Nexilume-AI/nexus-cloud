"""Personal legacy imports, real local commands and no commercial catalog."""
from decimal import Decimal
from io import StringIO
from unittest.mock import patch
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError
from apps.metrics import registry, services, urls, component_host, metric_validation
from apps.metrics.models import AlertRule, AlertEvent, MetricSnapshot, MonitoringHeartbeat
from nexus_personal import monitoring_http, monitoring_registry, monitoring_workers
from nexus_personal.services import provision_owner


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PRODUCTION=False, NEXUS_MONITOR_SMTP_CONFIGURED=False)
class PersonalMonitoringCompositionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.instance = provision_owner(email="composition-owner@example.test", password="fixture-monitor-composition-92473!")

    def test_legacy_imports_select_real_operational_host_without_report_functions(self):
        self.assertIs(services, monitoring_http)
        self.assertIs(registry, monitoring_registry)
        self.assertIs(component_host.configured_component("services"), monitoring_http)
        for name in ("send_report_now", "list_report_schedules", "list_report_deliveries", "get_system_metrics_for_tenant"):
            self.assertFalse(hasattr(services, name), name)
        routes = {str(item.pattern) for item in urls.urlpatterns}
        self.assertEqual(len(routes), 15)
        self.assertIn("metrics/actions/<str:kind>/<str:record_id>/", routes)
        self.assertIn("metrics/requests/<str:request_id>/", routes)
        self.assertFalse(any("reports" in route or "prometheus" in route or "diagnostics" in route for route in routes))

    def test_operational_registry_rejects_finance_and_unsupported_scopes(self):
        names = {row["metric"] for row in registry.definitions(True)}
        self.assertIn("system.usage.tokens", names)
        for name in ("system.usage.amount", "system.wallet.balance", "system.counts.api_keys"):
            self.assertNotIn(name, registry.DEFINITIONS)
            with self.assertRaises(ValidationError):
                registry.validate(metric=name, threshold="1")
        with self.assertRaises(ValidationError):
            registry.validate(metric="usage.requests", threshold="1", resource_type="api_key")
        self.assertEqual(registry.validate(metric="error_rate", threshold="10%"), (Decimal(10), "%"))
        for threshold in ("NaN", "Infinity", "-1%", "101%", "not-number"):
            with self.subTest(threshold=threshold), self.assertRaises(ValidationError):
                registry.validate(metric="error_rate", threshold=threshold)
        for channels in ({"unknown": []}, {"emails": ["invalid"]}, {"emails": ["a@example.test"]*11}):
            with self.subTest(channels=channels), self.assertRaises(ValidationError):
                registry.validate(metric="usage.requests", threshold="1", notification_channels=channels)
        with patch.object(metric_validation, "validate_metric", wraps=metric_validation.validate_metric) as shared:
            registry.validate(metric="usage.requests", threshold="1")
        shared.assert_called_once()

    def test_legacy_tasks_bind_real_operational_tasks_but_not_reports(self):
        from apps.metrics import tasks, operational_tasks
        self.assertIs(tasks, operational_tasks)
        self.assertFalse(hasattr(tasks, "send_scheduled_reports_task"))
        self.assertEqual(tasks.capture_metric_snapshots_task.name, "apps.metrics.operational_tasks.collect_snapshots")
        self.assertEqual(tasks.capture_metric_snapshots_task.run(), 1)
        self.assertEqual(MetricSnapshot.objects.count(), 1)

    def test_local_scheduler_collects_evaluates_and_audits_real_state(self):
        rule = AlertRule.objects.create(tenant=self.instance.tenant, project=self.instance.project,
            created_by=self.instance.owner, metric="system.counts.agents", threshold="0", threshold_value=0)
        stderr = StringIO()
        call_command("run_observability_worker", mode="scheduler", once=True, stderr=stderr)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(MetricSnapshot.objects.count(), 1)
        self.assertTrue(AlertEvent.objects.filter(rule=rule, status="firing", is_test=False).exists())
        self.assertEqual(set(MonitoringHeartbeat.objects.values_list("component", flat=True)), {"collector", "evaluator"})
        self.assertEqual(set(component_host.configured_component("local").jobs("scheduler")), {"collector", "evaluator", "retention"})

    def test_local_delivery_preserves_unconfigured_pending_outbox(self):
        rule = AlertRule.objects.create(tenant=self.instance.tenant, project=self.instance.project,
            created_by=self.instance.owner, metric="system.counts.agents", threshold="0", threshold_value=0)
        monitoring_workers.evaluate_rules()
        event = AlertEvent.objects.get(rule=rule)
        self.assertTrue(event.notifications.exists())
        call_command("run_observability_worker", mode="delivery", once=True)
        self.assertEqual(set(event.notifications.values_list("delivery_status", flat=True)), {"pending"})
        self.assertEqual(MonitoringHeartbeat.objects.get(component="delivery").last_error_code, "EMAIL_NOT_CONFIGURED")

    def test_local_failure_is_redacted_and_does_not_skip_other_components(self):
        stderr = StringIO()
        with patch.object(monitoring_workers, "collect_snapshots", side_effect=RuntimeError("secret-fixture")):
            call_command("run_observability_worker", mode="scheduler", once=True, stderr=stderr)
        self.assertIn("collector failed", stderr.getvalue())
        self.assertNotIn("secret-fixture", stderr.getvalue())
        self.assertTrue(MonitoringHeartbeat.objects.filter(component="evaluator").exists())

    def test_dataset_command_runs_actual_personal_maintenance_without_report_imports(self):
        stderr = StringIO()
        call_command("run_dataset_maintenance", once=True, stderr=stderr)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(AlertRule.objects.filter(project=self.instance.project, metric__startswith="data_assets.").count(), 5)
        self.assertTrue(MonitoringHeartbeat.objects.filter(component="dataset_evaluator").exists())

    @override_settings(NEXUS_PRODUCTION=True)
    def test_local_commands_are_not_a_production_worker_substitute(self):
        for command in ("run_dataset_maintenance", "run_observability_worker"):
            with self.subTest(command=command), self.assertRaises(CommandError):
                call_command(command, once=True)
        self.assertFalse(MetricSnapshot.objects.exists())
        self.assertFalse(MonitoringHeartbeat.objects.exists())
