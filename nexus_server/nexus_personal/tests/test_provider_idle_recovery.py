"""Personal owner Source eligibility after an inconclusive model probe."""
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.utils import timezone
from apps.deployments.candidates import resolve_model_source_deployment
from apps.providers.runtime_services import _apply_model_probe
from .deployment_guard_fixture import PersonalDeploymentGuardFixture


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1")
class PersonalProviderIdleRecoveryTests(PersonalDeploymentGuardFixture, TestCase):
    def test_long_idle_source_selection_keeps_stop_and_disable_fences(self):
        source = self.routing_deployment()
        offer, runtime = source.runtime_model_offer, source.provider_runtime
        now = timezone.now()
        verified = now - timedelta(days=30)
        offer.metadata = {"health_probe": {"last_definitive_status": "healthy", "last_definitive_at": verified.isoformat()}}
        offer.health_status = "unknown"
        offer.last_health_check_at = verified
        with patch("apps.providers.runtime_services._recent_model_success_at", return_value=None):
            _apply_model_probe(offer, (None, "Probe timed out."), now)
        offer.save()
        self.assertEqual(resolve_model_source_deployment(tenant=self.tenant, source=source).pk, source.pk)
        runtime.status = "stopped"
        runtime.save()
        self.assertIsNone(resolve_model_source_deployment(tenant=self.tenant, source=source))
        runtime.status = "active"
        runtime.save()
        offer.status = "disabled"
        offer.save()
        self.assertIsNone(resolve_model_source_deployment(tenant=self.tenant, source=source))
