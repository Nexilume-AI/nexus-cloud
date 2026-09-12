"""Shared freshness checks through actual owner-authenticated monitoring HTTP."""
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.metrics.models import MonitoringHeartbeat
from nexus_personal.services import provision_owner
from tests.monitoring_health_guards import MonitoringHealthGuards
from .test_installation import PASSWORD


@override_settings(ROOT_URLCONF="nexus_personal.urls",
                   EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend")
class PersonalMonitoringHealthTests(MonitoringHealthGuards, TestCase):
    def setUp(self):
        row = provision_owner(email="health-owner@example.test", password=PASSWORD)
        self.tenant = row.tenant
        self.now = timezone.now()
        for name in ("collector", "evaluator", "delivery"):
            MonitoringHeartbeat.objects.create(tenant=self.tenant, component=name,
                                               last_success_at=self.now)
        self.client = APIClient()
        token = Token.objects.create(user=row.owner)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token.key)
        self.headers = {"HTTP_X_NEXUS_TENANT": str(row.tenant.pk),
                        "HTTP_X_NEXUS_PROJECT": str(row.project.pk)}

    def monitoring_health_payload(self):
        response = self.client.get("/api/v1/metrics/system/", **self.headers)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        return response.json()["monitoring"]
