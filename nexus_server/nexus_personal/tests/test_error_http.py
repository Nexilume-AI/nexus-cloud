"""Personal production error contract consumed by the existing Web client."""
from unittest.mock import patch
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.providers.models import ProviderAccount, ProviderRuntimeAccount
from apps.gateway.provider_adapters import ProviderClientError
from nexus_personal.services import provision_owner
from . import test_provider_lifecycle_recovery as recovery
from .test_installation import PASSWORD


@override_settings(ROOT_URLCONF="nexus_personal.urls")
class PersonalErrorHTTPTests(TestCase):
    create_connection = recovery.PersonalProviderLifecycleRecoveryTests.create_connection

    def setUp(self):
        self.installation = provision_owner(email="error-owner@example.test", password=PASSWORD)
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.installation.owner)
        response = self.client.get("/api/v1/public/bootstrap/")
        self.assertEqual(response.status_code, 200, response.content)
        self.headers = {"HTTP_X_CSRFTOKEN": self.client.cookies["csrftoken"].value,
                        "HTTP_ORIGIN": "http://testserver"}

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content)
        payload = response.json()
        self.assertIs(payload.get("ok"), False, payload)
        self.assertIsNone(payload["data"])
        self.assertEqual(payload["error"]["code"], code)
        self.assertTrue(payload["error"]["message"])
        self.assertIn("request_id", payload)
        self.assertNotIn("secret-provider-key", response.content.decode())

    def test_running_provider_error_has_actionable_code_without_test_override(self):
        created = self.create_connection()
        account = ProviderAccount.objects.get(pk=created["id"])
        runtime = account.source_runtime_accounts.get()
        runtime.status = ProviderRuntimeAccount.STATUS_ACTIVE
        runtime.save(update_fields=["status", "updated_at"])
        response = self.client.patch(f"/api/v1/provider-connections/{account.pk}/",
            {"engine": "cliproxyapi", "upstream_provider": "claude"}, format="json", **self.headers)
        self.assert_error(response, 409, "PROVIDER_MUST_BE_STOPPED")
        account.refresh_from_db()
        runtime.refresh_from_db()
        self.assertEqual(account.provider.name, "openai-compatible")
        self.assertEqual(runtime.runtime_type, "direct_api")

    def test_validation_error_keeps_field_explanation(self):
        response = self.client.post("/api/v1/provider-connections/",
            {"engine": "unsupported"}, format="json", **self.headers)
        self.assert_error(response, 400, "VALIDATION_ERROR")
        self.assertIn("engine", response.json()["error"]["message"])
        self.assertFalse(ProviderAccount.objects.exists())

    @patch("apps.providers.connection_services.verify_openai_compatible_credentials")
    def test_failed_rotation_is_safe_and_keeps_existing_credentials(self, verify):
        account = ProviderAccount.objects.get(pk=self.create_connection()["id"])
        previous = (account.url, account.encrypted_key)
        verify.side_effect = ProviderClientError("Bearer upstream-sensitive-marker")
        response = self.client.patch(f"/api/v1/provider-connections/{account.pk}/",
            {"url": "https://replacement.example.test/v1", "key": "new-sensitive-key"},
            format="json", **self.headers)
        self.assert_error(response, 400, "PROVIDER_CREDENTIAL_VERIFICATION_FAILED")
        for secret in ("upstream-sensitive-marker", "new-sensitive-key"):
            self.assertNotIn(secret, response.content.decode())
        account.refresh_from_db()
        self.assertEqual((account.url, account.encrypted_key), previous)

    def test_busy_stop_is_structured_and_does_not_dispatch(self):
        account = ProviderAccount.objects.get(pk=self.create_connection(engine="codex_proxy")["id"])
        runtime = account.source_runtime_accounts.get()
        runtime.status = ProviderRuntimeAccount.STATUS_STARTING
        runtime.save(update_fields=["status", "updated_at"])
        with patch("apps.providers.runtime_services.get_provider_runtime_runner") as runner:
            response = self.client.post(f"/api/v1/provider-connections/{account.pk}/stop/",
                {}, format="json", **self.headers)
            self.assert_error(response, 409, "PROVIDER_RUNTIME_BUSY")
            runner.assert_not_called()
        runtime.refresh_from_db()
        self.assertEqual(runtime.status, ProviderRuntimeAccount.STATUS_STARTING)
