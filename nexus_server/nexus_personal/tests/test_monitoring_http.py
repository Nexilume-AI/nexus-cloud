"""Owner HTTP monitoring with real facts/outbox, under the private import guard."""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.agents.models import Agent, AgentRuntimeInvocation
from apps.audit.models import AuditLog
from apps.datasets.models import Dataset, DatasetImportJob
from apps.deployments.models import CanonicalModel, ModelGroup
from apps.gateway.models import GatewayRequestLog
from apps.jobs.models import Job
from apps.metrics.models import AlertRule, AlertEvent, AlertNotification, MonitoringHeartbeat, MetricSnapshot
from apps.routers.models import Router
from apps.tenancy.models import Project, Tenant
from nexus_personal.services import provision_owner


@override_settings(ROOT_URLCONF="nexus_personal.urls")
class PersonalMonitoringHttpTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.installation = provision_owner(email="metrics-owner@example.test", password="fixture-metrics-owner-28394!")
        cls.owner, cls.tenant, cls.project = cls.installation.owner, cls.installation.tenant, cls.installation.project
        cls.other = get_user_model().objects.create_user(username="metrics-other")
        cls.foreign_tenant = Tenant.objects.create(name="Foreign")
        cls.foreign_project = Project.objects.create(tenant=cls.foreign_tenant, name="Foreign")
        cls.agent = Agent.objects.create(tenant=cls.tenant, project=cls.project, name="Observed Agent")
        cls.router = Router.objects.create(tenant=cls.tenant, project=cls.project, name="Observed Router")
        cls.dataset = Dataset.objects.create(tenant=cls.tenant, project=cls.project, name="Observed Dataset", size_bytes=123, file_count=2)
        canonical = CanonicalModel.objects.create(key="observed-model", display_name="Observed model")
        cls.group = ModelGroup.objects.create(tenant=cls.tenant, project=cls.project, name="observed-model", canonical_model=canonical)
        cls.foreign_agent = Agent.objects.create(tenant=cls.foreign_tenant, project=cls.foreign_project, name="foreign-secret")
        cls.foreign_rule = AlertRule.objects.create(tenant=cls.foreign_tenant, project=cls.foreign_project,
            metric="system.usage.requests", threshold="1", threshold_value=1)
        cls.foreign_event = AlertEvent.objects.create(tenant=cls.foreign_tenant, rule=cls.foreign_rule,
            metric="system.usage.requests", value=1, triggered_at=timezone.now())
        for status, latency, tokens in (("success", 10, 20), ("failed", 30, 40)):
            GatewayRequestLog.objects.create(tenant=cls.tenant, project_id=str(cls.project.pk),
                router=cls.router, model="test-model", status=status, latency_ms=latency, total_tokens=tokens,
                fallback_count=2 if status == "failed" else 0)
            AgentRuntimeInvocation.objects.create(tenant=cls.tenant, project=cls.project,
                agent=cls.agent, status=status, latency_ms=latency)
        AgentRuntimeInvocation.objects.create(tenant=cls.tenant, project=cls.project, agent=cls.agent, status="pending")
        GatewayRequestLog.objects.create(tenant=cls.foreign_tenant, project_id=str(cls.foreign_project.pk), model="secret", total_tokens=999)
        Job.objects.create(tenant=cls.tenant, project=cls.project, job_type="fixture", status="failed")
        DatasetImportJob.objects.create(tenant=cls.tenant, dataset=cls.dataset, request_key="monitor-import",
            request_hash="monitor-import", kind="upload", state="queued")

    def setUp(self):
        self.client = APIClient()
        self.client.force_login(self.owner)

    def create_rule(self, **overrides):
        response = self.client.post("/api/v1/alerts/", {"metric": "system.usage.requests", "threshold": "2", **overrides}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return AlertRule.objects.get(pk=response.data["id"])

    def test_capability_catalog_only_advertises_executable_operational_metrics(self):
        response = self.client.get("/api/v1/metrics/capabilities/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["read"])
        self.assertTrue(response.data["manage"])
        self.assertFalse(response.data["financial"])
        self.assertFalse(response.data["reports"])
        names = {row["metric"] for row in response.data["metrics"]}
        self.assertIn("data_assets.queued", names)
        self.assertIn("system.quality.p95_latency_ms", names)
        self.assertFalse(names & {"system.usage.amount", "system.wallet.balance", "system.counts.api_keys"})
        self.assertNotIn("api_key", {kind for row in response.data["metrics"] for kind in row["resource_types"]})
        self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_system_reads_actual_requests_counts_jobs_and_data_assets(self):
        response = self.client.get("/api/v1/metrics/system/")
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data
        self.assertEqual(data["usage"], {"requests": 4, "tokens": 60})
        self.assertEqual(data["window"]["usage"], data["usage"])
        self.assertEqual(data["window"]["quality"]["error_rate"], 50)
        self.assertEqual(data["window"]["quality"]["fallbacks"], 2)
        self.assertAlmostEqual(data["window"]["surfaces"]["gateway"]["p95_latency_ms"], 29)
        self.assertEqual(data["counts"]["agents"], 1)
        self.assertEqual(data["counts"]["datasets"], 1)
        self.assertEqual(data["counts"]["model_groups"], 1)
        self.assertEqual(data["data_assets"]["queued"], 1)
        self.assertGreater(data["data_assets"]["spool_free_bytes"], 0)
        self.assertEqual(data["summary"]["failed_jobs"], 1)
        self.assertEqual(data["monitoring"]["state"], "unknown")
        self.assertIsNone(data["monitoring"]["last_updated_at"])
        self.assertEqual(data["monitoring"]["affected_areas"], ["metrics", "alerts", "notifications"])
        self.assertEqual(response["Cache-Control"], "private, no-store")
        for forbidden in ("wallet", "ledger", "amount", "cost", "api_keys", "report_schedules", "foreign-secret"):
            self.assertNotIn('"' + forbidden + '"', response.content.decode())
        GatewayRequestLog.objects.create(tenant=self.tenant, project_id=str(self.project.pk), model="new", total_tokens=7)
        self.assertEqual(self.client.get("/api/v1/metrics/system/").data["usage"]["tokens"], 67)

    def test_missing_stale_error_and_test_transport_heartbeats_never_promise_health(self):
        now = timezone.now()
        for component in ("collector", "evaluator", "delivery"):
            MonitoringHeartbeat.objects.create(tenant=self.tenant, component=component, last_success_at=now)
        self.assertEqual(self.client.get("/api/v1/metrics/system/").data["monitoring"]["state"], "degraded")
        with override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend"):
            healthy = self.client.get("/api/v1/metrics/system/").data["monitoring"]
            self.assertEqual(healthy["state"], "healthy")
            self.assertEqual(healthy["affected_areas"], [])
            MonitoringHeartbeat.objects.filter(tenant=self.tenant, component="evaluator").update(last_success_at=now - timedelta(hours=1))
            self.assertEqual(self.client.get("/api/v1/metrics/system/").data["monitoring"]["state"], "stale")
            MonitoringHeartbeat.objects.filter(tenant=self.tenant, component="evaluator").update(last_success_at=now, last_error_code="FAILED")
            degraded = self.client.get("/api/v1/metrics/system/").data["monitoring"]
            self.assertEqual(degraded["state"], "degraded")
            self.assertNotIn("components", degraded)

    def test_resource_details_use_real_nonfinancial_records_not_saved_json(self):
        MetricSnapshot.objects.create(tenant=self.tenant, resource_type="agent", resource_id=str(self.agent.pk),
            metrics_json={"cost": "secret-cost", "secret": "private-snapshot"})
        for kind, obj in (("agent", self.agent), ("router", self.router), ("dataset", self.dataset), ("model", self.group)):
            response = self.client.get("/api/v1/metrics/", {"resource": kind, "id": str(obj.pk)})
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data["resource_id"], str(obj.pk))
            self.assertNotIn("private-snapshot", response.content.decode())
            self.assertNotIn("secret-cost", response.content.decode())
            if kind in ("agent", "router"):
                self.assertEqual(response.data["window"]["usage"]["requests"], 2)
                self.assertEqual(response.data["runtime"]["failed_count"], 1)
        self.assertEqual(self.client.get("/api/v1/metrics/?resource=dataset&id=" + str(self.dataset.pk)).data["size_bytes"], 123)
        self.assertEqual(self.client.get("/api/v1/metrics/?resource=model&id=observed-model").status_code, 200)

    def test_window_parameter_and_unsupported_resources_fail_explicitly(self):
        for seconds in ("0", "59", "604801", "true", "bad"):
            self.assertEqual(self.client.get("/api/v1/metrics/system/", {"window_seconds": seconds}).status_code, 400)
        for kind, identifier in (("api_key", uuid4()), ("unknown", uuid4()), ("agent", "")):
            self.assertEqual(self.client.get("/api/v1/metrics/", {"resource": kind, "id": str(identifier)}).status_code, 400)

    def test_owner_context_and_resource_isolation_for_all_new_routes(self):
        for path in (f"/api/v1/metrics/?resource=agent&id={self.foreign_agent.pk}",
                     "/api/v1/metrics/?resource=agent&id=not-a-uuid",
                     f"/api/v1/alerts/{self.foreign_rule.pk}/", f"/api/v1/alerts/events/{self.foreign_event.pk}/",
                     "/api/v1/alerts/events/?rule=not-a-uuid"):
            self.assertEqual(self.client.get(path).status_code, 404, path)
        self.assertEqual(self.client.post(f"/api/v1/alerts/{self.foreign_rule.pk}/test/").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/v1/alerts/{self.foreign_rule.pk}/").status_code, 404)
        for path in ("/api/v1/metrics/capabilities/", "/api/v1/metrics/system/", "/api/v1/alerts/", "/api/v1/alerts/events/"):
            for headers in ({"HTTP_X_NEXUS_PROJECT": str(self.foreign_project.pk)},
                            {"HTTP_X_NEXUS_TENANT": str(self.foreign_tenant.pk)}):
                self.assertIn(self.client.get(path, **headers).status_code, (401, 403))
            self.client.force_login(self.other)
            self.assertIn(self.client.get(path).status_code, (401, 403))
            self.client.logout()
            self.assertIn(self.client.get(path).status_code, (401, 403))
            self.client.force_login(self.owner)

    def test_create_test_persisted_notification_delete_and_history(self):
        rule = self.create_rule(notification_channels={"emails": ["test@example.test", "TEST@example.test"]})
        self.assertEqual(rule.project_id, self.project.pk)
        self.assertEqual(rule.created_by_id, self.owner.pk)
        response = self.client.post(f"/api/v1/alerts/{rule.pk}/test/")
        self.assertEqual(response.status_code, 201, response.data)
        event = AlertEvent.objects.get(pk=response.data["id"])
        self.assertEqual(event.value, Decimal(4))
        self.assertTrue(event.is_test)
        self.assertEqual(event.status, "resolved")
        self.assertEqual(event.notifications.count(), 1)
        notification = event.notifications.get()
        self.assertEqual(notification.delivery_status, "pending")
        self.assertIsNone(notification.sent_at)
        self.assertEqual(notification.target, "test@example.test")
        self.assertEqual(self.client.get("/api/v1/metrics/system/").data["summary"]["firing_alerts"], 0)
        self.assertEqual(self.client.delete(f"/api/v1/alerts/{rule.pk}/").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/alerts/").data, [])
        self.assertEqual(self.client.get(f"/api/v1/alerts/events/{event.pk}/").status_code, 200)
        self.assertEqual(self.client.post(f"/api/v1/alerts/{rule.pk}/test/").status_code, 404)
        self.assertTrue(AuditLog.objects.filter(resource_id=str(rule.pk), action="metrics.alert.test").exists())

    def test_gauge_and_surface_percentile_alerts_read_actual_values(self):
        for metric, expected, scope in (("system.counts.agents", 1, {}), ("data_assets.queued", 1, {}),
                ("system.quality.p95_latency_ms", 29, {"resource_type": "agent", "resource_id": str(self.agent.pk)})):
            rule = self.create_rule(metric=metric, **scope)
            response = self.client.post(f"/api/v1/alerts/{rule.pk}/test/")
            self.assertEqual(response.status_code, 201, response.data)
            self.assertEqual(Decimal(response.data["value"]), Decimal(expected))

    def test_invalid_financial_scope_and_no_data_tests_create_nothing(self):
        for payload in ({"metric": "system.usage.amount"}, {"metric": "wallet.balance"},
                        {"metric": "system.counts.api_keys"}, {"resource_type": "api_key", "resource_id": str(uuid4())},
                        {"resource_id": str(self.agent.pk)}, {"window_seconds": 604801},
                        {"metric": "system.quality.error_rate", "threshold": "101%"},
                        {"threshold": "NaN"}, {"notification_channels": {"emails": ["not-an-email"]}},
                        {"resource_type": "agent", "resource_id": str(self.foreign_agent.pk)}):
            response = self.client.post("/api/v1/alerts/", {"metric": "system.usage.requests", "threshold": "2", **payload}, format="json")
            self.assertIn(response.status_code, (400, 404), response.data)
        self.assertFalse(AlertRule.objects.filter(tenant=self.tenant).exists())
        empty = Agent.objects.create(tenant=self.tenant, project=self.project, name="No samples")
        rule = self.create_rule(metric="system.quality.p95_latency_ms", resource_type="agent", resource_id=str(empty.pk))
        response = self.client.post(f"/api/v1/alerts/{rule.pk}/test/")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("NO_DATA", str(response.data))
        self.assertFalse(rule.events.exists())

    def test_test_event_and_notification_roll_back_if_audit_fails(self):
        rule = self.create_rule()
        with patch("nexus_personal.monitoring_http._audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertLogs("nexus_personal.exceptions", level="ERROR") as captured:
                response = self.client.post(f"/api/v1/alerts/{rule.pk}/test/")
        self.assertNotIn("audit unavailable", "\n".join(captured.output))
        self.assertTrue(all(record.exc_info is None and record.stack_info is None for record in captured.records))
        self.assertEqual(response.status_code, 500, response.content)
        self.assertIs(response.data["ok"], False)
        self.assertEqual(response.data["error"]["code"], "INTERNAL_SERVER_ERROR")
        self.assertNotIn("audit unavailable", response.content.decode())
        self.assertFalse(rule.events.exists())
        self.assertFalse(AlertNotification.objects.filter(tenant=self.tenant).exists())

    def test_alert_history_is_paginated_without_cross_scope_or_silent_truncation(self):
        AlertRule.objects.bulk_create([AlertRule(tenant=self.tenant, project=self.project,
            metric="system.usage.requests", threshold="1") for _ in range(105)])
        self.assertEqual(self.client.get("/api/v1/alerts/").status_code, 400)
        seen, cursor = set(), None
        while True:
            response = self.client.get("/api/v1/alerts/", {"limit": 40, **({"cursor": cursor} if cursor else {})})
            self.assertEqual(response.status_code, 200, response.data)
            ids = {row["id"] for row in response.data["items"]}
            self.assertFalse(ids & seen)
            seen.update(ids)
            cursor = response.data["next_cursor"]
            if not cursor:
                break
        self.assertEqual(len(seen), 105)
        self.assertNotIn(str(self.foreign_rule.pk), seen)

    def test_system_query_count_does_not_scale_with_resource_count(self):
        with CaptureQueriesContext(connection) as baseline:
            self.assertEqual(self.client.get("/api/v1/metrics/system/").status_code, 200)
        Agent.objects.bulk_create([Agent(tenant=self.tenant, project=self.project, name=f"Agent {i}") for i in range(150)])
        with CaptureQueriesContext(connection) as large:
            response = self.client.get("/api/v1/metrics/system/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["counts"]["agents"], 151)
        self.assertLessEqual(len(large), len(baseline))
        self.assertLess(len(large), 55)

    def test_financial_report_and_privileged_exporter_routes_remain_absent(self):
        for path in ("reports/schedules/", "reports/deliveries/", "metrics/prometheus/", "metrics/diagnostics/"):
            self.assertEqual(self.client.get("/api/v1/" + path).status_code, 404, path)
