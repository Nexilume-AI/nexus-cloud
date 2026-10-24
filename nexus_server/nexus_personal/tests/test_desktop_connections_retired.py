"""Personal Cloud also rejects the retired desktop integration."""
from django.test import SimpleTestCase
from django.urls import Resolver404, resolve
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIRequestFactory
from nexus_personal.authentication import PersonalBearerAuthentication


class RetiredPersonalDesktopTests(SimpleTestCase):
    def test_old_desktop_token_is_rejected_without_database_access(self):
        request = APIRequestFactory().get(
            "/api/v1/agents/", HTTP_AUTHORIZATION="Bearer nd-nexus-retired-test"
        )
        with self.assertRaisesMessage(AuthenticationFailed, "no longer supported"):
            PersonalBearerAuthentication().authenticate(request)

    def test_desktop_routes_are_removed(self):
        connection = "00000000-0000-4000-8000-000000000000"
        for suffix in ("", "start/", *(f"{connection}/{action}/" for action in (
            "exchange", "approve", "sign-in", "session", "agents", "select-agent", "revoke"
        ))):
            with self.subTest(suffix=suffix), self.assertRaises(Resolver404):
                resolve("/api/v1/desktop-connections/" + suffix, urlconf="nexus_personal.urls")
