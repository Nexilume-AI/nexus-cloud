from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings
from rest_framework import exceptions

from apps.accounts.identity import (
    ExternalIdentityError, _backend, authenticate_external_user,
    authenticate_machine_token, authenticate_api_key, identity_backend, password_context,
)
from apps.accounts.personal_identity import PersonalIdentityBackend
from apps.common.authentication import NexusBearerAuthentication


class InvalidContextBackend:
    def password_context(self, **kwargs):
        return {"tenant": "invented"}

    def authenticate_external_user(self, **kwargs):
        raise RuntimeError("backend unavailable")

    def authenticate_machine_token(self, **kwargs):
        raise RuntimeError("backend unavailable")

    def authenticate_api_key(self, **kwargs):
        raise RuntimeError("backend unavailable")


class IdentityExtensionTests(SimpleTestCase):
    def setUp(self):
        _backend.cache_clear()
        self.addCleanup(_backend.cache_clear)

    def test_missing_or_incomplete_backend_is_not_an_allow_all_fallback(self):
        for path in ("", "missing.Identity", "builtins.dict"):
            with self.subTest(path=path), override_settings(NEXUS_IDENTITY_BACKEND=path):
                with self.assertRaises(ImproperlyConfigured):
                    identity_backend()

    @override_settings(NEXUS_IDENTITY_BACKEND="tests.test_identity_extension.InvalidContextBackend")
    def test_bad_context_and_backend_errors_do_not_fall_back(self):
        with self.assertRaises(ImproperlyConfigured):
            password_context(request=object(), user=object())
        with self.assertRaises(RuntimeError):
            authenticate_external_user(email="someone@example.test")
        with self.assertRaises(RuntimeError):
            authenticate_machine_token(request=object(), token="sa-nexus-test-only")
        with self.assertRaises(RuntimeError):
            authenticate_api_key(request=object(), token="sk-nexus-test-only")



    def test_legacy_oauth_error_alias_is_the_same_exception_class(self):
        from apps.accounts.google_oauth import GoogleOAuthError
        self.assertIs(GoogleOAuthError, ExternalIdentityError)


@override_settings(
    NEXUS_IDENTITY_BACKEND="apps.accounts.personal_identity.PersonalIdentityBackend",
    NEXUS_PERSONAL_OWNER_ID="123",
    NEXUS_PERSONAL_TENANT_ID="personal-context",
    NEXUS_PERSONAL_PROJECT_ID="personal-project",
)
class PersonalIdentityTests(SimpleTestCase):
    def setUp(self):
        _backend.cache_clear()
        self.addCleanup(_backend.cache_clear)
        self.owner = SimpleNamespace(pk=123, is_active=True)

    def test_owner_uses_fixed_context_even_without_headers(self):
        self.assertEqual(password_context(request=SimpleNamespace(), user=self.owner), ("personal-context", "personal-project"))
        self.assertEqual(password_context(request=SimpleNamespace(tenant_id="personal-context", project_id="personal-project"), user=self.owner),
                         ("personal-context", "personal-project"))

    def test_other_user_or_disabled_owner_cannot_enter(self):
        for user in (SimpleNamespace(pk=124, is_active=True), SimpleNamespace(pk=123, is_active=False), SimpleNamespace()):
            with self.subTest(user=user), self.assertRaises(exceptions.AuthenticationFailed):
                password_context(request=SimpleNamespace(), user=user)

    def test_context_headers_cannot_select_another_owner_scope(self):
        for request in (SimpleNamespace(tenant_id="other"), SimpleNamespace(project_id="other"),
                        SimpleNamespace(tenant_id="personal-context", project_id="other")):
            with self.subTest(request=request), self.assertRaises(exceptions.PermissionDenied):
                password_context(request=request, user=self.owner)

    def test_public_oauth_cannot_register_or_link_even_with_open_signup(self):
        for provider in ("google", "github"):
            with self.subTest(provider=provider), self.assertRaises(ExternalIdentityError) as raised:
                authenticate_external_user(provider=provider, subject="claimed-owner", email="owner@example.test", signup_mode="open")
            self.assertEqual(raised.exception.code, "PERSONAL_EXTERNAL_SIGNIN_DISABLED")

    def test_machine_token_has_no_private_import_or_fallback(self):
        with self.assertRaises(exceptions.AuthenticationFailed):
            authenticate_machine_token(request=SimpleNamespace(), token="sa-nexus-fixture")

    def test_legacy_key_has_no_private_import_or_fallback(self):
        with self.assertRaisesMessage(exceptions.AuthenticationFailed, 'Legacy API keys are not supported'):
            authenticate_api_key(request=SimpleNamespace(), token="sk-nexus-fixture")

    def test_stale_profile_cannot_supply_an_unpinned_token_context(self):
        self.owner.account_profile = SimpleNamespace(tenant_id="other", project_id="other")
        with self.assertRaises(ImproperlyConfigured):
            password_context(request=SimpleNamespace(), user=self.owner)
        self.owner.account_profile = SimpleNamespace(tenant_id="personal-context", project_id="personal-project")
        self.assertEqual(password_context(request=SimpleNamespace(), user=self.owner), ("personal-context", "personal-project"))
        with override_settings(NEXUS_PERSONAL_PROJECT_ID=""):
            with self.assertRaises(ImproperlyConfigured):
                password_context(request=SimpleNamespace(), user=self.owner)

    def test_missing_owner_or_context_is_a_configuration_failure(self):
        for value in ("", None, 123):
            with self.subTest(value=value), override_settings(NEXUS_PERSONAL_OWNER_ID=value):
                with self.assertRaises(ImproperlyConfigured):
                    password_context(request=SimpleNamespace(), user=self.owner)
        with override_settings(NEXUS_PERSONAL_TENANT_ID=""):
            with self.assertRaises(ImproperlyConfigured):
                password_context(request=SimpleNamespace(), user=self.owner)

    @override_settings(NEXUS_PERSONAL_PROJECT_ID="")
    def test_optional_personal_project_does_not_allow_arbitrary_project(self):
        self.assertEqual(password_context(request=SimpleNamespace(), user=self.owner), ("personal-context", ""))
        with self.assertRaises(exceptions.PermissionDenied):
            password_context(request=SimpleNamespace(project_id="invented"), user=self.owner)
