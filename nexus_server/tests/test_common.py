from __future__ import annotations

from django.test import TestCase
from rest_framework.test import APIClient


class CommonAPITests(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()

    def test_health_endpoint_returns_ok(self) -> None:
        response = self.client.get("/api/v1/health/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIs(payload["ok"], True)
        self.assertEqual(payload["data"], {"status": "ok"})
        self.assertIsNone(payload["error"])

    def test_request_id_exists(self) -> None:
        response = self.client.get("/api/v1/health/")
        payload = response.json()

        self.assertTrue(payload["request_id"].startswith("req_"))
        self.assertEqual(response["X-Request-ID"], payload["request_id"])

    def test_unauthenticated_protected_endpoint_fails(self) -> None:
        response = self.client.get("/api/v1/auth/whoami/")

        self.assertEqual(response.status_code, 401)
        payload = response.json()
        self.assertIs(payload["ok"], False)
        self.assertIsNone(payload["data"])
        self.assertEqual(payload["error"]["code"], "NOT_AUTHENTICATED")
        self.assertTrue(payload["request_id"].startswith("req_"))

    def test_public_bootstrap_is_safe_and_establishes_csrf_cookie(self) -> None:
        response = self.client.get(
            "/api/v1/public/bootstrap/",
            HTTP_X_NEXUS_TENANT="spoofed-tenant",
            HTTP_X_NEXUS_PROJECT="spoofed-project",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"]
        self.assertIs(payload["anonymous_access"], True)
        self.assertEqual(payload["authentication_mode"], "on_demand")
        self.assertIs(payload["session_authenticated"], False)
        self.assertNotIn("tenant_id", payload)
        self.assertNotIn("project_id", payload)
        self.assertNotIn("user", payload)
        self.assertIn("csrftoken", response.cookies)
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_spoofed_context_headers_do_not_authenticate(self) -> None:
        response = self.client.get(
            "/api/v1/auth/whoami/",
            HTTP_X_NEXUS_TENANT="spoofed-tenant",
            HTTP_X_NEXUS_PROJECT="spoofed-project",
        )

        self.assertEqual(response.status_code, 401)
