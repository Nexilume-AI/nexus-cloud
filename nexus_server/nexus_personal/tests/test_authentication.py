import time
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts.models import AccountProfile
from apps.common.context import get_tenant_context, reset_tenant_context, set_tenant_context
from apps.common.jwt import decode_jwt, encode_jwt, issue_token_pair
from nexus_personal.authentication import validate_owner
from nexus_personal.middleware import PersonalContextMiddleware
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


class PersonalRequestTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="owner@example.test", password=PASSWORD)
        cls.other = get_user_model().objects.create_user(username="not-owner", password=PASSWORD)

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)

    def tokens(self, user=None):
        return issue_token_pair(user=user or self.row.owner, tenant_id=str(self.row.tenant_id),
                                project_id=str(self.row.project_id))

    def bearer(self, token, **headers):
        return self.client.get("/owner/", HTTP_AUTHORIZATION="Bearer " + token, **headers)

    def test_password_login_real_jwt_and_browser_session_use_fixed_context(self):
        response = self.client.post("/login/", {"email": "owner@example.test", "password": PASSWORD}, format="json")
        self.assertEqual(response.status_code, 200)
        token = response.json()["access_token"]
        self.assertEqual(decode_jwt(token)["tenant_id"], str(self.row.tenant_id))
        session_response = self.client.get("/owner/")
        self.assertEqual(session_response.status_code, 200)
        self.assertEqual(session_response.json()["user"], str(self.row.owner_id))
        self.client.logout()
        response = self.bearer(token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.json()["tenant"], response.json()["project"]),
                         (str(self.row.tenant_id), str(self.row.project_id)))

    def test_session_writes_still_require_csrf(self):
        self.client.force_login(self.row.owner)
        self.assertEqual(self.client.post("/owner/", {}).status_code, 403)
        csrf = self.client.get("/owner/").json()["csrf"]
        self.client.cookies["csrftoken"] = csrf
        self.assertEqual(self.client.post("/owner/", {}, HTTP_X_CSRFTOKEN=csrf).status_code, 200)

    def test_other_users_session_and_tokens_never_become_owner(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get("/owner/").status_code, 401)
        self.client.logout()
        for token in (Token.objects.create(user=self.other).key, self.tokens(self.other)["access_token"]):
            self.assertEqual(self.bearer(token).status_code, 401)

    def test_current_owner_drf_token_works_without_private_identity_app(self):
        self.assertEqual(self.bearer(Token.objects.create(user=self.row.owner).key).status_code, 200)

    def test_context_headers_cannot_select_another_scope(self):
        token = self.tokens()["access_token"]
        for header in ("HTTP_X_NEXUS_TENANT", "HTTP_X_NEXUS_PROJECT"):
            response = self.bearer(token, **{header: "another-scope"})
            self.assertEqual(response.status_code, 403)
            self.assertNotIn("another-scope", response.content.decode())
        self.assertEqual(self.bearer(token, HTTP_X_NEXUS_TENANT=str(self.row.tenant_id),
                                     HTTP_X_NEXUS_PROJECT=str(self.row.project_id)).status_code, 200)

    def test_signed_wrong_context_and_refresh_jwt_are_rejected(self):
        self.assertEqual(self.bearer(self.tokens()["refresh_token"]).status_code, 401)
        for key in ("tenant_id", "project_id", "user_id"):
            payload = decode_jwt(self.tokens()["access_token"])
            payload[key] = "another"
            self.assertEqual(self.bearer(encode_jwt(payload)).status_code, 401)

    def test_expired_missing_or_malformed_claims_fail_closed(self):
        original = decode_jwt(self.tokens()["access_token"])
        for field in ("exp", "iat", "iss", "aud", "sub", "user_id", "typ", "tenant_id", "project_id"):
            payload = {key: value for key, value in original.items() if key != field}
            with self.subTest(missing=field):
                self.assertEqual(self.bearer(encode_jwt(payload)).status_code, 401)
        for fields in ({"exp": int(time.time()) - 1}, {"exp": []}, {"exp": True},
                       {"iat": int(time.time()) + 1000}, {"sub": "not-an-integer", "user_id": "not-an-integer"}):
            self.assertEqual(self.bearer(encode_jwt({**original, **fields})).status_code, 401)

    def test_bad_credentials_do_not_fall_back_to_valid_session(self):
        self.client.force_login(self.row.owner)
        for token in ("bad.token.value", "sa-nexus-secret", "sk-nexus-secret", "x" * 16385):
            response = self.bearer(token)
            self.assertEqual(response.status_code, 401)
            self.assertNotIn(token, response.content.decode())
        self.assertEqual(self.client.get("/owner/", HTTP_X_API_KEY="sk-nexus-secret").status_code, 401)

    def test_deleted_profile_cannot_be_recreated_by_request_authentication(self):
        token = self.tokens()["access_token"]
        AccountProfile.objects.filter(user=self.row.owner).delete()
        self.assertEqual(self.bearer(token).status_code, 401)
        self.assertFalse(AccountProfile.objects.filter(user=self.row.owner).exists())

    def test_disabled_owner_or_context_invalidates_existing_credentials(self):
        token = self.tokens()["access_token"]
        for model, pk, field in ((get_user_model(), self.row.owner_id, "is_active"),
                                  (type(self.row.project), self.row.project_id, "status")):
            model.objects.filter(pk=pk).update(**{field: False if field == "is_active" else "disabled"})
            response = self.bearer(token)
            self.assertEqual(response.status_code, 503)
            model.objects.filter(pk=pk).update(**{field: True if field == "is_active" else "active"})

    def test_context_is_reset_even_if_view_raises(self):
        previous = set_tenant_context("outer-tenant", "outer-project")
        def explode(request):
            self.assertEqual(get_tenant_context().tenant_id, str(self.row.tenant_id))
            raise RuntimeError("fixture")
        try:
            with self.assertRaises(RuntimeError):
                PersonalContextMiddleware(explode)(RequestFactory().get("/owner/"))
            self.assertEqual(get_tenant_context().tenant_id, "outer-tenant")
        finally:
            reset_tenant_context(previous)

    def test_cached_profile_cannot_bypass_current_database_context(self):
        stale_user = get_user_model().objects.select_related("account_profile").get(pk=self.row.owner_id)
        AccountProfile.objects.filter(user=stale_user).update(status="disabled")
        from rest_framework.exceptions import AuthenticationFailed
        with self.assertRaises(AuthenticationFailed):
            validate_owner(SimpleNamespace(META={}), stale_user)
