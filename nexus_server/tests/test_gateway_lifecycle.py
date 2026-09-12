"""No-database contract tests; a missing distribution must never grant free use."""
from unittest.mock import Mock, patch
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings
from apps.common import gateway_lifecycle as lifecycle


class IncompleteLifecycle:
    def reserve(self, **kwargs):
        return None


OPERATIONS = {
    "estimate_gateway_charge": "estimate",
    "calculate_cost": "calculate",
    "reserve_gateway_charge": "reserve",
    "release_gateway_charge_reservation": "release",
    "finalize_gateway_success": "finalize",
    "apply_success_side_effects": "record",
    "lock_image_account": "lock_image_account",
    "expire_image_operations": "expire_images"
}


class GatewayLifecycleTests(SimpleTestCase):
    def setUp(self):
        lifecycle._backend.cache_clear()
        self.addCleanup(lifecycle._backend.cache_clear)

    def test_missing_or_incomplete_backend_rejects_every_operation(self):
        for path in ("", "missing.Backend", "tests.test_gateway_lifecycle.IncompleteLifecycle"):
            for name in OPERATIONS:
                with self.subTest(path=path, operation=name), override_settings(NEXUS_GATEWAY_LIFECYCLE_BACKEND=path):
                    with self.assertRaises(ImproperlyConfigured):
                        getattr(lifecycle, name)()

    def test_exact_forwarding_including_reservation_identity(self):
        for name, method in OPERATIONS.items():
            with self.subTest(operation=name):
                backend = Mock()
                arguments = {"request": object(), "reservation": object(), "cost": object()}
                result = object()
                getattr(backend, method).return_value = result
                with patch.object(lifecycle, "_current", return_value=backend):
                    self.assertIs(getattr(lifecycle, name)(**arguments), result)
                getattr(backend, method).assert_called_once_with(**arguments)

    def test_failure_never_retries_dispatch_or_silently_skips_release(self):
        for name, method in OPERATIONS.items():
            with self.subTest(operation=name):
                backend = Mock()
                failure = RuntimeError("policy unavailable")
                getattr(backend, method).side_effect = failure
                with patch.object(lifecycle, "_current", return_value=backend), self.assertRaises(RuntimeError) as raised:
                    getattr(lifecycle, name)(request_id="one-shot")
                self.assertIs(raised.exception, failure)
                self.assertEqual(len(backend.mock_calls), 1)

    @override_settings(NEXUS_GATEWAY_LIFECYCLE_BACKEND="configured.Backend")
    def test_backend_load_is_lazy_and_cached(self):
        backend = Mock()
        with patch.object(lifecycle, "import_string", return_value=lambda: backend) as load:
            lifecycle.reserve_gateway_charge()
            lifecycle.expire_image_operations()
        load.assert_called_once_with("configured.Backend")

    def test_image_pricing_retains_positional_compatibility_and_fails_closed(self):
        deployment, payload, result = object(), object(), object()
        backend = Mock()
        backend.image_price.return_value = result
        with patch.object(lifecycle, "_current", return_value=backend):
            self.assertIs(lifecycle.image_price(deployment, "images.generate", payload), result)
        backend.image_price.assert_called_once_with(deployment=deployment, operation="images.generate", payload=payload)
        with override_settings(NEXUS_GATEWAY_LIFECYCLE_BACKEND=""), self.assertRaises(ImproperlyConfigured):
            lifecycle.image_price(deployment, "images.generate", payload)
