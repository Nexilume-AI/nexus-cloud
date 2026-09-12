"""Timestamped real invocation facts, not billing fixtures or mocked aggregates."""
from datetime import timedelta
from uuid import uuid4
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import APIException
from apps.agents.models import Agent, AgentRuntimeInvocation
from apps.gateway.models import GatewayRequestLog
from apps.routers.models import Router
from apps.tenancy.models import Tenant, Project
from apps.metrics.registry import window_metrics
from nexus_personal.services import provision_owner


class PersonalMonitoringWindowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.installation = provision_owner(email="window-owner@example.test", password="fixture-window-owner-5928!")
        cls.tenant, cls.project = cls.installation.tenant, cls.installation.project
        cls.router = Router.objects.create(tenant=cls.tenant, project=cls.project, name="Router")
        cls.agent = Agent.objects.create(tenant=cls.tenant, project=cls.project, name="Agent")
        cls.end = timezone.now()
        cls.start = cls.end - timedelta(minutes=5)
        for latency, state, stamp, tokens in ((10, "success", cls.start, 10),
                (30, "failed", cls.end - timedelta(seconds=1), 20),
                (999, "success", cls.end, 999),
                (999, "failed", cls.start - timedelta(microseconds=1), 999)):
            row = GatewayRequestLog.objects.create(tenant=cls.tenant, project_id=str(cls.project.pk),
                model="fixture", router=cls.router, latency_ms=latency, status=state,
                total_tokens=tokens, fallback_count=2 if state == "failed" else 0)
            GatewayRequestLog.objects.filter(pk=row.pk).update(created_at=stamp)
        for latency, state in ((100, "success"), (300, "failed"), (999, "pending")):
            row = AgentRuntimeInvocation.objects.create(tenant=cls.tenant, project=cls.project,
                agent=cls.agent, latency_ms=latency, status=state)
            AgentRuntimeInvocation.objects.filter(pk=row.pk).update(created_at=cls.start)

    def window(self, **options):
        return window_metrics(self.tenant, start=self.start, end=self.end, project_id=str(self.project.pk), **options)

    def test_counts_error_rate_tokens_and_surface_percentiles(self):
        data = self.window()
        self.assertEqual(data["usage"], {"requests": 4, "tokens": 30})
        self.assertEqual(data["quality"], {"failed": 2, "error_rate": 50.0, "fallbacks": 2})
        self.assertTrue(data["has_samples"])
        self.assertEqual(data["surfaces"]["gateway"]["avg_latency_ms"], 20)
        self.assertAlmostEqual(data["surfaces"]["gateway"]["p95_latency_ms"], 29)
        self.assertAlmostEqual(data["surfaces"]["gateway"]["p99_latency_ms"], 29.8)
        self.assertAlmostEqual(data["surfaces"]["agent"]["p95_latency_ms"], 290)
        self.assertNotIn("p95_latency_ms", data["quality"])
        self.assertEqual(data["period_start"], self.start.isoformat())
        self.assertEqual(data["period_end"], self.end.isoformat())
        for forbidden in ("amount", "cost", "wallet", "ledger"):
            self.assertNotIn(forbidden, str(data))

    def test_resource_windows_do_not_mix_surfaces_or_pending_invocations(self):
        agent = self.window(resource_type="agent", resource_id=str(self.agent.pk))
        router = self.window(resource_type="router", resource_id=str(self.router.pk))
        self.assertEqual(agent["usage"], {"requests": 2, "tokens": 0})
        self.assertEqual(router["usage"], {"requests": 2, "tokens": 30})
        self.assertIsNone(agent["surfaces"]["gateway"]["p95_latency_ms"])
        self.assertIsNone(router["surfaces"]["agent"]["p99_latency_ms"])

    def test_no_samples_and_single_sample_are_not_invented_health(self):
        empty = window_metrics(self.tenant, start=self.end + timedelta(seconds=1),
            end=self.end + timedelta(seconds=61), project_id=str(self.project.pk))
        self.assertFalse(empty["has_samples"])
        self.assertEqual(empty["usage"]["requests"], 0)
        self.assertIsNone(empty["quality"]["error_rate"])
        self.assertIsNone(empty["surfaces"]["gateway"]["avg_latency_ms"])
        single = window_metrics(self.tenant, start=self.end - timedelta(seconds=2),
            end=self.end, project_id=str(self.project.pk))
        self.assertEqual(single["surfaces"]["gateway"]["p95_latency_ms"], 30)
        self.assertEqual(single["surfaces"]["gateway"]["p99_latency_ms"], 30)

    def test_foreign_context_or_resource_cannot_be_aggregated(self):
        foreign_tenant = Tenant.objects.create(name="Foreign")
        foreign_project = Project.objects.create(tenant=foreign_tenant, name="Foreign")
        foreign_router = Router.objects.create(tenant=foreign_tenant, project=foreign_project, name="Foreign")
        for tenant, project in ((foreign_tenant, foreign_project.pk), (self.tenant, foreign_project.pk)):
            with self.assertRaises(APIException):
                window_metrics(tenant, project_id=str(project))
        for resource_type, resource_id in (("router", foreign_router.pk), ("agent", uuid4()),
                                          ("api_key", uuid4()), ("unknown", "")):
            with self.assertRaises(APIException):
                self.window(resource_type=resource_type, resource_id=str(resource_id))

    def test_invalid_windows_and_resource_identifiers_are_rejected(self):
        for options in ({"seconds": 0}, {"seconds": 604801}, {"seconds": True},
                        {"seconds": "invalid"}, {"end": ""}, {"start": False},
                        {"start": self.end, "end": self.start},
                        {"start": self.end.replace(tzinfo=None), "end": self.end},
                        {"resource_type": "router"}, {"resource_type": "router", "resource_id": "invalid"},
                        {"resource_type": "system", "resource_id": str(self.router.pk)}):
            with self.subTest(options=options), self.assertRaises(APIException):
                window_metrics(self.tenant, project_id=str(self.project.pk), **options)

    def test_recent_window_never_substitutes_lifetime_totals(self):
        recent = window_metrics(self.tenant, seconds=1, end=self.end, project_id=str(self.project.pk))
        self.assertEqual(recent["usage"], {"requests": 1, "tokens": 20})
        self.assertEqual(recent["quality"]["error_rate"], 100.0)

    def test_omitted_project_does_not_include_other_project_logs(self):
        other = Project.objects.create(tenant=self.tenant, name="Unrelated")
        row = GatewayRequestLog.objects.create(tenant=self.tenant, project_id=str(other.pk),
            model="other", status="failed", total_tokens=9999)
        GatewayRequestLog.objects.filter(pk=row.pk).update(created_at=self.start)
        result = window_metrics(self.tenant, start=self.start, end=self.end)
        self.assertEqual(result["usage"], {"requests": 4, "tokens": 30})
        self.assertEqual(result["scope"], "project")

    def test_large_window_has_bounded_query_count_and_no_financial_columns(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        rows = [GatewayRequestLog(tenant=self.tenant, project_id=str(self.project.pk),
            model="large", status="success", latency_ms=value) for value in range(1000)]
        GatewayRequestLog.objects.bulk_create(rows)
        GatewayRequestLog.objects.filter(model="large").update(created_at=self.start)
        with CaptureQueriesContext(connection) as captured:
            result = self.window()
        self.assertEqual(result["usage"]["requests"], 1004)
        self.assertLessEqual(len(captured), 7)
        for query in captured:
            self.assertNotIn('SUM("gateway_gatewayrequestlog"."cost")', query["sql"])
            self.assertNotIn("billing_", query["sql"])

    def test_missing_backend_is_not_an_implicit_zero_cost_success(self):
        from django.core.exceptions import ImproperlyConfigured
        with override_settings(NEXUS_MONITORING_WINDOW_FUNCTION=""), self.assertRaises(ImproperlyConfigured):
            self.window()
