from __future__ import annotations

from django.test import TestCase
from rest_framework.test import APIClient


class OpenAPISchemaTests(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()

    def test_schema_endpoint_returns_native_openapi(self) -> None:
        response = self.client.get("/api/v1/schema/", HTTP_ACCEPT="application/json")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["openapi"].split(".")[0], "3")
        self.assertIn("/api/v1/api-keys/", payload["paths"])
        self.assertIn("/api/v1/service-accounts/", payload["paths"])
        self.assertNotIn("ok", payload)

    def test_schema_includes_nexus_security_schemes(self) -> None:
        response = self.client.get("/api/v1/schema/", HTTP_ACCEPT="application/json")

        schemes = response.json()["components"]["securitySchemes"]
        self.assertIn("BearerAuth", schemes)
        self.assertIn("TenantHeader", schemes)
        self.assertIn("ProjectHeader", schemes)
        self.assertIn("ApiKeyHeader", schemes)
        self.assertEqual(schemes["TenantHeader"]["name"], "X-Nexus-Tenant")

    def test_schema_does_not_expose_stored_secret_fields(self) -> None:
        response = self.client.get("/api/v1/schema/", HTTP_ACCEPT="application/json")

        text = response.content.decode()
        self.assertNotIn("encrypted_key", text)
        self.assertNotIn("encrypted_password", text)
        self.assertNotIn("key_hash", text)
        self.assertNotIn("token_hash", text)
