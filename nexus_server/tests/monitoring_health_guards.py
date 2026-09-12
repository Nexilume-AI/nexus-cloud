"""Freshness and safe email-health assertions common to both distributions."""
from datetime import timedelta

from django.test import override_settings

from apps.metrics.models import MonitoringHeartbeat


class MonitoringHealthGuards:
    def test_missing_success_does_not_invent_a_collection_timestamp(self):
        MonitoringHeartbeat.objects.filter(tenant=self.tenant, component="collector").delete()
        data = self.monitoring_health_payload()
        self.assertEqual(data["state"], "unknown")
        self.assertIsNone(data["last_updated_at"])
        self.assertEqual(data["affected_areas"], ["metrics"])

    def test_stale_timestamp_is_the_oldest_completion_not_browser_check_time(self):
        old = self.now - timedelta(hours=1)
        MonitoringHeartbeat.objects.filter(tenant=self.tenant, component="collector").update(last_success_at=old)
        data = self.monitoring_health_payload()
        self.assertEqual(data["state"], "stale")
        self.assertEqual(data["last_updated_at"], old.isoformat())
        self.assertNotEqual(data["last_updated_at"], data["as_of"])

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_test_only_email_warns_customer_without_exposing_transport(self):
        data = self.monitoring_health_payload()
        self.assertEqual(data["state"], "degraded")
        self.assertEqual(data["affected_areas"], ["notifications"])
        self.assertNotIn("delivery_transport", data)
