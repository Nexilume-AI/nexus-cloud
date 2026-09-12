"""Exercise the product account routes, not the test login/owner probes."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import AccountProfile
from apps.audit.models import AuditLog
from apps.common.jwt import decode_jwt
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


@override_settings(ROOT_URLCONF="nexus_personal.urls")
class PersonalAccountHTTPTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="account-owner@example.test", password=PASSWORD)

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)

    def bootstrap(self, client=None):
        client = client or self.client
        result = client.get("/api/v1/public/bootstrap/")
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result["Cache-Control"], "no-store")
        self.assertIn("csrftoken", client.cookies)
        return result.data

    def csrf(self, client=None):
        return {"HTTP_X_CSRFTOKEN": (client or self.client).cookies["csrftoken"].value}

    def login(self, client=None, password=PASSWORD):
        client = client or self.client
        self.bootstrap(client)
        result = client.post("/api/v1/auth/login/", {"email": self.row.owner.email, "password": password},
            format="json", HTTP_ORIGIN="http://testserver", HTTP_X_NEXUS_CLIENT="web", **self.csrf(client))
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data, {"tenant_id": str(self.row.tenant_id), "session_authenticated": True})
        return result

    def test_bootstrap_browser_login_and_logout_use_owner_session(self):
        anonymous = self.bootstrap()
        self.assertEqual(anonymous["distribution"], "community")
        self.assertFalse(anonymous["anonymous_access"])
        self.assertFalse(anonymous["session_authenticated"])
        self.assertFalse(anonymous["authentication"]["google"]["enabled"])
        self.assertFalse(anonymous["authentication"]["github"]["enabled"])
        self.assertNotIn(self.row.owner.email, str(anonymous))
        self.login()
        self.assertTrue(self.bootstrap()["session_authenticated"])
        response = self.client.get("/api/v1/auth/whoami/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["user_id"], str(self.row.owner_id))
        self.assertFalse(response.data["is_superuser"])
        response = self.client.post("/api/v1/auth/logout/", {}, format="json", **self.csrf())
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(self.bootstrap()["session_authenticated"])
        self.assertEqual(self.client.get("/api/v1/auth/whoami/").status_code, 401)

    def test_login_origin_csrf_and_input_validation(self):
        body = {"email": self.row.owner.email, "password": PASSWORD}
        path = "/api/v1/auth/login/"
        self.assertEqual(self.client.post(path, body, format="json", HTTP_ORIGIN="http://testserver").status_code, 403)
        self.bootstrap()
        self.assertEqual(self.client.post(path, body, format="json", HTTP_ORIGIN="https://foreign.test",
                                         **self.csrf()).status_code, 403)
        self.assertEqual(self.client.post(path, {"email": self.row.owner.email}, format="json").status_code, 400)
        invalid = self.client.post(path, {**body, "password": "wrong-secret"}, format="json")
        self.assertEqual(invalid.status_code, 403)
        self.assertNotIn("wrong-secret", str(invalid.data))
        self.assertFalse(self.bootstrap()["session_authenticated"])

    def test_personal_context_is_owner_only_fixed_and_read_only(self):
        path = '/api/v1/personal/context/'
        self.assertEqual(self.client.get(path).status_code, 401)
        self.login()
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual([item['id'] for item in response.data['tenants']], [str(self.row.tenant_id)])
        self.assertEqual([item['id'] for item in response.data['projects']], [str(self.row.project_id)])
        self.assertNotIn(self.row.owner.email, str(response.data))
        self.assertEqual(self.client.get(path, HTTP_X_NEXUS_TENANT='foreign').status_code, 403)
        self.assertEqual(self.client.get(path, HTTP_X_NEXUS_PROJECT='foreign').status_code, 403)
        self.assertEqual(self.client.post(path, {}, format='json', **self.csrf()).status_code, 405)
        other = get_user_model().objects.create_user(username='not-the-owner', password=PASSWORD)
        other_client = APIClient()
        other_client.force_login(other)
        self.assertEqual(other_client.get(path).status_code, 401)

    def test_api_login_produces_real_jwt_for_fixed_personal_context(self):
        result = self.client.post("/api/v1/auth/login/", {"email": self.row.owner.email, "password": PASSWORD}, format="json")
        self.assertEqual(result.status_code, 200, result.data)
        payload = decode_jwt(result.data["access_token"])
        self.assertEqual(payload["tenant_id"], str(self.row.tenant_id))
        self.assertEqual(payload["project_id"], str(self.row.project_id))
        isolated = APIClient()
        result = isolated.get("/api/v1/auth/whoami/", HTTP_AUTHORIZATION="Bearer " + result.data["access_token"])
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data["email"], self.row.owner.email)

    def test_profile_writes_require_csrf_and_preserve_context(self):
        self.login()
        path = "/api/v1/account/me/"
        fields = {"display_name": "个人实例", "phone": "", "company": "Local"}
        self.assertEqual(self.client.patch(path, fields, format="json").status_code, 403)
        response = self.client.patch(path, fields, format="json", **self.csrf())
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["display_name"], fields["display_name"])
        self.assertEqual(response.data["project_id"], str(self.row.project_id))
        self.assertEqual(self.client.get(path).data["display_name"], fields["display_name"])
        self.assertEqual(self.client.patch(path, fields, format="json", HTTP_X_NEXUS_PROJECT="foreign",
                                         **self.csrf()).status_code, 403)
        self.assertEqual(AccountProfile.objects.get(user=self.row.owner).project_id, str(self.row.project_id))

    def test_change_password_preserves_this_session_invalidates_other_session(self):
        second = APIClient(enforce_csrf_checks=True)
        self.login()
        self.login(second)
        path = "/api/v1/account/change-password/"
        new = "replacement-owner-local-strong-29184!"
        fields = {"current_password": PASSWORD, "new_password": new, "new_password_confirm": new}
        for body in ({**fields, "current_password": "incorrect"},
                     {**fields, "new_password_confirm": "different"},
                     {**fields, "new_password": "short", "new_password_confirm": "short"}):
            self.assertEqual(self.client.post(path, body, format="json", **self.csrf()).status_code, 400)
        response = self.client.post(path, fields, format="json", **self.csrf())
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, {"status": "password_changed"})
        self.assertEqual(self.client.get("/api/v1/auth/whoami/").status_code, 200)
        self.assertEqual(second.get("/api/v1/auth/whoami/").status_code, 401)
        audit = AuditLog.objects.get(action="accounts.password.change")
        self.assertNotIn(new, str(audit.metadata))
        self.assertNotIn(PASSWORD, str(audit.metadata))
        self.row.owner.refresh_from_db()
        self.assertTrue(self.row.owner.check_password(new))

    def test_foreign_session_is_not_reported_as_the_owner(self):
        other = get_user_model().objects.create_user(username="other-account", email="other-account@example.test")
        self.client.force_login(other)
        self.assertEqual(self.client.get("/api/v1/public/bootstrap/").status_code, 403)
        self.assertEqual(self.client.get("/api/v1/account/me/").status_code, 401)

    def test_no_http_signup_recovery_secret_or_commercial_management_routes(self):
        from django.urls import Resolver404, resolve
        for path in ("/api/v1/auth/register/", "/api/v1/auth/google/start/", "/api/v1/auth/github/start/",
                     "/api/v1/account/password-reset/", "/api/v1/access/capabilities/",
                     "/api/v1/billing/plans/", "/api/v1/marketplace/agents/", "/owner/", "/login/"):
            with self.subTest(path=path), self.assertRaises(Resolver404):
                resolve(path)

    @override_settings(
        NEXUS_GOOGLE_OAUTH_SIGNUP_MODE="open",
        NEXUS_GITHUB_OAUTH_SIGNUP_MODE="open",
        NEXUS_GITHUB_OAUTH_CLIENT_ID="personal-boundary-fixture-client",
        NEXUS_GITHUB_OAUTH_CLIENT_SECRET="personal-boundary-fixture-secret",
        NEXUS_GITHUB_OAUTH_REDIRECT_URI="https://oauth.example.test/callback/",
    )
    def test_inherited_oauth_configuration_cannot_enable_signup_or_replace_owner(self):
        from unittest.mock import patch
        from apps.accounts.models import ExternalIdentity
        from apps.tenancy.models import Project, Tenant

        models = (get_user_model(), AccountProfile, ExternalIdentity, Tenant, Project)
        before = {model._meta.label: list(model.objects.order_by("pk").values_list("pk", flat=True))
                  for model in models}
        with patch("apps.accounts.views.exchange_google_identity") as google, \
             patch("apps.accounts.views.exchange_github_identity") as github:
            for signed_in in (False, True):
                if signed_in:
                    self.login()
                bootstrap = self.bootstrap()
                for provider in ("google", "github"):
                    self.assertFalse(bootstrap["authentication"][provider]["enabled"])
                    for action in ("start", "callback"):
                        with self.subTest(signed_in=signed_in, provider=provider, action=action):
                            response = self.client.get(f"/api/v1/auth/{provider}/{action}/", {
                                "code": "untrusted-callback-fixture", "state": "untrusted-state",
                                "next": "/agents",
                            })
                            self.assertEqual(response.status_code, 404)
                            self.assertNotIn("Location", response)
                            self.assertNotIn(f"nexus_{provider}_oauth", self.client.session)
                response = self.client.post("/api/v1/auth/register/", {
                    "email": "uninvited@example.test", "password": PASSWORD,
                }, format="json", HTTP_ORIGIN="http://testserver", **self.csrf())
                self.assertEqual(response.status_code, 404)
            google.assert_not_called()
            github.assert_not_called()
        self.assertEqual(before, {model._meta.label: list(model.objects.order_by("pk").values_list("pk", flat=True))
                                  for model in models})
        self.row.refresh_from_db()
        self.assertEqual(self.client.get("/api/v1/auth/whoami/").data["user_id"], str(self.row.owner_id))


@override_settings(ROOT_URLCONF="nexus_personal.urls")
class PersonalUninitializedHTTPTests(TestCase):
    def test_missing_owner_returns_setup_required_without_creating_users(self):
        client = APIClient()
        response = client.get("/api/v1/public/bootstrap/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "PERSONAL_SETUP_REQUIRED")
        self.assertFalse(get_user_model().objects.exists())
