from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from apps.common.authorization import _backend, has_nexus_permission
from apps.common.catalog_throttles import CatalogThrottle


class ExplicitAllowBackend:
    def has_permission(self, *args):
        return True


class InvalidReturnBackend:
    def has_permission(self, *args):
        return "allowed"


class BrokenBackend:
    def has_permission(self, *args):
        raise RuntimeError("unavailable")


class CoreExtensionTests(SimpleTestCase):
    def setUp(self):
        _backend.cache_clear()
        self.user = SimpleNamespace(is_authenticated=True)

    def test_anonymous_never_reaches_backend(self):
        with override_settings(NEXUS_AUTHORIZATION_BACKEND="missing.package.Backend"):
            self.assertFalse(has_nexus_permission(SimpleNamespace(is_authenticated=False), None, "admin"))

    @override_settings(NEXUS_AUTHORIZATION_BACKEND="tests.test_core_extension_points.ExplicitAllowBackend")
    def test_explicit_configured_backend_grant(self):
        self.assertTrue(has_nexus_permission(self.user, "tenant", "agent.use", "agent", "id"))

    @override_settings(NEXUS_AUTHORIZATION_BACKEND="tests.test_core_extension_points.InvalidReturnBackend")
    def test_truthy_values_do_not_grant(self):
        self.assertFalse(has_nexus_permission(self.user, "tenant", "admin"))

    def test_missing_and_invalid_backend_fail_closed(self):
        for path in ("", "missing.package.Backend", "builtins.dict"):
            with self.subTest(path=path), override_settings(NEXUS_AUTHORIZATION_BACKEND=path):
                with self.assertRaises(ImproperlyConfigured):
                    has_nexus_permission(self.user, "tenant", "admin")

    @override_settings(NEXUS_AUTHORIZATION_BACKEND="tests.test_core_extension_points.BrokenBackend")
    def test_unavailable_backend_does_not_fall_back(self):
        with self.assertRaises(RuntimeError):
            has_nexus_permission(self.user, "tenant", "admin")



    @override_settings(NEXUS_MARKETPLACE_RATE="73/min")
    def test_throttle_preserves_legacy_configuration_and_private_cache_key(self):
        throttle = CatalogThrottle()
        self.assertEqual(throttle.get_rate(), "73/min")
        request = SimpleNamespace(user=SimpleNamespace(pk=None), META={"REMOTE_ADDR": "192.0.2.123"})
        key = throttle.get_cache_key(request, None)
        self.assertNotIn("192.0.2.123", key)
        self.assertIn("public_catalog", key)

    @override_settings(NEXUS_CATALOG_RATE="15/min", NEXUS_MARKETPLACE_RATE="73/min")
    def test_core_throttle_setting_has_priority(self):
        self.assertEqual(CatalogThrottle().get_rate(), "15/min")



    def test_resource_policy_missing_or_invalid_fails_closed(self):
        from apps.common.resource_limits import enforce_tenant_resource_quota
        for path in ("", "missing.package.Policy", "builtins.dict"):
            with self.subTest(path=path), override_settings(NEXUS_RESOURCE_ADMISSION_BACKEND=path):
                with self.assertRaises(ImproperlyConfigured):
                    enforce_tenant_resource_quota(tenant="space", resource="remote_workspace")
