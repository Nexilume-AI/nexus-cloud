"""Personal code may import the commerce boundary but never settle via a shim."""
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase
from apps.providers.commerce import OPERATIONS, invoke_provider_commerce
from apps.providers.credentials import issue_provider_export_key
from apps.providers import pool_services


class PersonalProviderBoundaryTests(SimpleTestCase):
    def test_missing_commerce_is_explicit_error_not_synthetic_receipt(self):
        for operation in OPERATIONS:
            with self.subTest(operation=operation), self.assertRaises(ImproperlyConfigured):
                invoke_provider_commerce(operation)

    def test_arbitrary_operation_cannot_be_selected(self):
        with self.assertRaises(ImproperlyConfigured):
            invoke_provider_commerce("__getattribute__", "__dict__")

    def test_missing_credential_issuer_never_returns_placeholder_key(self):
        with self.assertRaises(ImproperlyConfigured):
            issue_provider_export_key(request=object(), runtime=object(), model="model")

    def test_catalog_exports_cannot_silently_load_proprietary_code(self):
        for name in pool_services.EXPORTS:
            with self.subTest(name=name), self.assertRaises(ImproperlyConfigured):
                getattr(pool_services, name)
        with self.assertRaises(AttributeError):
            getattr(pool_services, "not_a_public_operation")
