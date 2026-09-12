from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import AccountProfile, PasswordResetToken
from apps.audit.models import AuditLog


class AccountAPITests(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(
            username="ada",
            email="ada@example.com",
            password="correct-password",
        )

    def test_login_with_correct_credentials_succeeds(self) -> None:
        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": "ada@example.com", "password": "correct-password"},
            format="json",
            HTTP_X_NEXUS_TENANT="tenant_1",
            HTTP_X_NEXUS_PROJECT="project_1",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIs(payload["ok"], True)
        self.assertIn("access_token", payload["data"])
        self.assertIn("refresh_token", payload["data"])
        self.assertEqual(payload["data"]["tenant_id"], "tenant_1")
        self.assertTrue(AccountProfile.objects.filter(user=self.user, tenant_id="tenant_1").exists())
        self.assertTrue(AuditLog.objects.filter(action="accounts.login", actor=self.user).exists())

    def test_login_with_wrong_password_fails(self) -> None:
        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": "ada@example.com", "password": "wrong-password"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertIs(payload["ok"], False)
        self.assertEqual(payload["error"]["code"], "AUTHENTICATION_FAILED")

    def test_unauthenticated_whoami_fails(self) -> None:
        response = self.client.get("/api/v1/auth/whoami/")

        self.assertEqual(response.status_code, 401)
        payload = response.json()
        self.assertIs(payload["ok"], False)
        self.assertEqual(payload["error"]["code"], "NOT_AUTHENTICATED")

    def test_authenticated_whoami_succeeds(self) -> None:
        token = self._login()["access_token"]

        response = self.client.get(
            "/api/v1/auth/whoami/",
            HTTP_AUTHORIZATION=f"Bearer {token}",
            HTTP_X_NEXUS_TENANT="tenant_1",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIs(payload["ok"], True)
        self.assertEqual(payload["data"]["user_id"], str(self.user.pk))
        self.assertEqual(payload["data"]["email"], "ada@example.com")
        self.assertEqual(payload["data"]["current_tenant"], "tenant_1")
        self.assertEqual(payload["data"]["roles"], [])

    def test_browser_session_authenticates_after_login(self) -> None:
        login_response = self.client.post(
            "/api/v1/auth/login/",
            {"email": "ada@example.com", "password": "correct-password"},
            format="json",
            HTTP_X_NEXUS_CLIENT="web",
        )
        login_payload = login_response.json()["data"]
        self.assertIs(login_payload["session_authenticated"], True)
        self.assertNotIn("access_token", login_payload)
        self.assertNotIn("refresh_token", login_payload)

        response = self.client.get("/api/v1/auth/whoami/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["email"], "ada@example.com")

    def test_vite_development_origin_can_create_browser_session_with_csrf(self) -> None:
        csrf_client = APIClient(enforce_csrf_checks=True)
        bootstrap_response = csrf_client.get("/api/v1/public/bootstrap/")
        csrf_token = bootstrap_response.cookies["csrftoken"].value

        login_response = csrf_client.post(
            "/api/v1/auth/login/",
            {"email": "ada@example.com", "password": "correct-password"},
            format="json",
            HTTP_ORIGIN="http://127.0.0.1:5173",
            HTTP_X_CSRFTOKEN=csrf_token,
            HTTP_X_NEXUS_CLIENT="web",
        )

        self.assertEqual(login_response.status_code, 200)
        self.assertIs(login_response.json()["data"]["session_authenticated"], True)

    def test_untrusted_browser_origin_cannot_create_session(self) -> None:
        csrf_client = APIClient(enforce_csrf_checks=True)
        bootstrap_response = csrf_client.get("/api/v1/public/bootstrap/")
        csrf_token = bootstrap_response.cookies["csrftoken"].value

        login_response = csrf_client.post(
            "/api/v1/auth/login/",
            {"email": "ada@example.com", "password": "correct-password"},
            format="json",
            HTTP_ORIGIN="https://untrusted.example",
            HTTP_X_CSRFTOKEN=csrf_token,
            HTTP_X_NEXUS_CLIENT="web",
        )

        self.assertEqual(login_response.status_code, 403)

    def test_logout_invalidates_browser_session(self) -> None:
        self.client.post(
            "/api/v1/auth/login/",
            {"email": "ada@example.com", "password": "correct-password"},
            format="json",
        )
        response = self.client.post("/api/v1/auth/logout/")
        self.assertEqual(response.status_code, 200)

        response = self.client.get("/api/v1/auth/whoami/")
        self.assertEqual(response.status_code, 401)

    def test_update_account_info_succeeds(self) -> None:
        token = self._login()["access_token"]

        response = self.client.patch(
            "/api/v1/account/me/",
            {
                "display_name": "Ada Lovelace",
                "phone": "+15550100",
                "company": "Analytical Engines Inc.",
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
            HTTP_X_NEXUS_TENANT="tenant_1",
            HTTP_X_NEXUS_PROJECT="project_1",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIs(payload["ok"], True)
        self.assertEqual(payload["data"]["display_name"], "Ada Lovelace")
        self.assertEqual(payload["data"]["phone"], "+15550100")
        self.assertEqual(payload["data"]["company"], "Analytical Engines Inc.")
        self.assertTrue(AuditLog.objects.filter(action="accounts.profile.update").exists())

    def test_authenticated_user_can_change_password_and_keep_browser_session(self) -> None:
        self.assertTrue(self.client.login(username="ada", password="correct-password"))

        response = self.client.post(
            "/api/v1/account/change-password/",
            {
                "current_password": "correct-password",
                "new_password": "a-new-secure-password-2026",
                "new_password_confirm": "a-new-secure-password-2026",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"], {"status": "password_changed"})
        self.assertNotIn("correct-password", response.content.decode())
        self.assertNotIn("a-new-secure-password-2026", response.content.decode())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("a-new-secure-password-2026"))
        whoami = self.client.get("/api/v1/auth/whoami/")
        self.assertEqual(whoami.status_code, 200)
        self.assertTrue(AuditLog.objects.filter(action="accounts.password.change", actor=self.user).exists())

    def test_change_password_rejects_wrong_current_password_and_mismatch(self) -> None:
        self.client.force_authenticate(self.user)

        wrong_current = self.client.post(
            "/api/v1/account/change-password/",
            {
                "current_password": "wrong-password",
                "new_password": "a-new-secure-password-2026",
                "new_password_confirm": "a-new-secure-password-2026",
            },
            format="json",
        )
        mismatch = self.client.post(
            "/api/v1/account/change-password/",
            {
                "current_password": "correct-password",
                "new_password": "a-new-secure-password-2026",
                "new_password_confirm": "another-secure-password-2026",
            },
            format="json",
        )

        self.assertEqual(wrong_current.status_code, 400)
        self.assertEqual(mismatch.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("correct-password"))
        self.assertFalse(AuditLog.objects.filter(action="accounts.password.change").exists())

    def test_password_reset_for_existing_user_returns_success(self) -> None:
        response = self.client.post(
            "/api/v1/account/password-reset/",
            {"email": "ada@example.com"},
            format="json",
            HTTP_X_NEXUS_TENANT="tenant_1",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertIs(payload["ok"], True)
        self.assertEqual(payload["data"]["status"], "reset_token_created")
        self.assertIn("reset_token_id", payload["data"])
        self.assertNotIn("reset_token", payload["data"])
        self.assertEqual(PasswordResetToken.objects.count(), 1)
        self.assertTrue(AuditLog.objects.filter(action="accounts.password_reset.request").exists())

    def _login(self) -> dict:
        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": "ada@example.com", "password": "correct-password"},
            format="json",
            HTTP_X_NEXUS_TENANT="tenant_1",
        )
        return response.json()["data"]
