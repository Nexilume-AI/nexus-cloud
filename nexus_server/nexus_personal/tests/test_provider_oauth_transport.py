"""Actual proxy HTTP OAuth exchanges on the existing Personal upstream fixture.

Only starting the fixed-port callback listener is mocked. The state registry,
owner session HTTP entrypoint, transports, activation, catalog discovery and model probes are real. This
does not claim real upstream account login or Docker/browser acceptance.
"""
import json
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from rest_framework.test import APIClient

from apps.common.crypto import encrypt_secret
from apps.providers import runtime_services as runtime_api
from apps.providers.models import Provider, ProviderAccount, ProviderRuntimeAccount
from apps.tenancy.models import Project, Tenant
from .provider_http_fixture import ProviderHTTPFixture


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1",
                   NEXUS_PROVIDER_REQUEST_TIMEOUT_SECONDS=5)
class PersonalProviderOAuthTransportTests(ProviderHTTPFixture, TestCase):
    @classmethod
    def configure_upstream(cls, base):
        class OAuthUpstream(base):
            def do_GET(self):
                if self.path not in {"/auth/login", "/v0/management/codex-auth-url", "/v0/management/anthropic-auth-url"}:
                    return super().do_GET()
                codex = self.path == "/auth/login"
                name, expected = (("Authorization", "Bearer local-provider-test-key") if codex else
                                  ("X-Management-Key", "mgmt-local-provider-test-key"))
                valid = self.headers.get(name) == expected
                cls.oauth_calls.append(("login", self.path, valid))
                if not valid:
                    self.send_error(401)
                    return
                url = "https://auth.example.test/authorize?state=" + cls.oauth_state
                if codex:
                    self.send_response(302)
                    self.send_header("Location", url)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    self.reply({} if cls.oauth_mode == "missing_url" else {"url": url})

            def do_POST(self):
                if self.path not in {"/auth/code-relay", "/v0/management/oauth-callback"}:
                    return super().do_POST()
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                codex = self.path == "/auth/code-relay"
                callback = payload.get("callbackUrl" if codex else "redirect_url", "")
                query = parse_qs(urlparse(callback).query)
                valid = query.get("state") == [cls.oauth_state] and query.get("code") == ["synthetic-code"]
                if codex:
                    valid = valid and self.headers.get("Authorization") == "Bearer local-provider-test-key"
                else:
                    valid = valid and payload.get("provider") == cls.oauth_provider
                cls.oauth_calls.append(("callback", self.path, valid))
                if not valid:
                    self.send_error(400)
                    return
                ok = cls.oauth_mode != "rejected"
                self.reply({"success": ok} if codex else {"status": "ok" if ok else "rejected"})
        return OAuthUpstream

    def setUp(self):
        super().setUp()
        type(self).oauth_calls = []
        type(self).oauth_mode = "success"
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.installation.owner)

    def runtime(self, engine="cliproxyapi", upstream="openai"):
        cls = type(self)
        cls.oauth_state = "personal-oauth-" + uuid4().hex
        cls.oauth_provider = "anthropic" if upstream == "claude" else "codex"
        state = cls.oauth_state
        def cleanup_state():
            with runtime_api._OAUTH_RELAY_LOCK:
                runtime_api._OAUTH_RELAY_STATES.pop(state, None)
        self.addCleanup(cleanup_state)
        provider, _ = Provider.objects.get_or_create(name=upstream, defaults={"display_name": upstream})
        source = ProviderAccount.objects.create(
            tenant=self.installation.tenant, created_by=self.installation.owner,
            provider=provider, account_id=state, auth_mode=ProviderAccount.AUTH_INTERACTIVE_LOGIN,
            login_status=ProviderAccount.LOGIN_REQUIRED)
        base = f"http://127.0.0.1:{self.upstream.server_port}"
        path = "/auth/login" if engine == "codex_proxy" else f"/v0/management/{cls.oauth_provider}-auth-url"
        runtime = ProviderRuntimeAccount.objects.create(
            tenant=self.installation.tenant, project=self.installation.project, owner=self.installation.owner,
            source_provider_account=source, name=state, runtime_type=engine,
            status=ProviderRuntimeAccount.STATUS_LOGIN_REQUIRED,
            encrypted_proxy_api_key=encrypt_secret("local-provider-test-key"),
            internal_login_url=base + path, internal_api_url=base + "/v1")
        return runtime

    def login(self, runtime):
        with patch("apps.providers.runtime_services.start_codex_oauth_callback_relay") as listener:
            response = self.client.get(self.login_path(runtime))
            self.assertEqual(response.status_code, 200, response.data)
            listener.assert_called_once_with(runtime=runtime)
        self.assertEqual(set(response.data), {
            "runtime_id", "runtime_type", "login_url", "public_login_path", "status"})
        self.assertEqual(response.data["runtime_id"], str(runtime.pk))
        self.assertEqual(response.data["runtime_type"], runtime.runtime_type)
        self.assertEqual(response.data["status"], ProviderRuntimeAccount.STATUS_LOGIN_REQUIRED)
        self.assertNotIn("local-provider-test-key", json.dumps(response.data))
        url = response.data["login_url"]
        self.assertEqual(parse_qs(urlparse(url).query)["state"], [type(self).oauth_state])
        self.assertNotIn("local-provider-test-key", url)
        return "http://localhost/auth/callback?code=synthetic-code&state=" + type(self).oauth_state

    @staticmethod
    def login_path(runtime):
        return f"/api/v1/provider-connections/{runtime.source_provider_account_id}/login/"

    def assert_login_denied_without_proxy_contact(self, client, runtime, statuses, **headers):
        with patch("apps.providers.runtime_services.start_codex_oauth_callback_relay") as listener:
            response = client.get(self.login_path(runtime), **headers)
            self.assertIn(response.status_code, statuses, response.json())
            listener.assert_not_called()
        self.assertNotIn(type(self).oauth_state, runtime_api._OAUTH_RELAY_STATES)
        self.assertEqual(self.oauth_calls, [])
        self.assertEqual(self.calls, [])
        self.assertNotIn("local-provider-test-key", json.dumps(response.json()))

    def test_login_http_requires_the_installation_owner(self):
        runtime = self.runtime()
        self.assert_login_denied_without_proxy_contact(APIClient(), runtime, (401, 403))
        other = APIClient()
        other.force_login(self.other)
        self.assert_login_denied_without_proxy_contact(other, runtime, (401, 403))

    def test_login_http_rejects_foreign_context_headers(self):
        runtime = self.runtime()
        for header in ("HTTP_X_NEXUS_TENANT", "HTTP_X_NEXUS_PROJECT"):
            with self.subTest(header=header):
                self.assert_login_denied_without_proxy_contact(
                    self.client, runtime, (400, 403), **{header: str(uuid4())})

    def test_login_http_rejects_a_foreign_source_account(self):
        runtime = self.runtime()
        ProviderAccount.objects.filter(pk=runtime.source_provider_account_id).update(created_by=self.other)
        self.assert_login_denied_without_proxy_contact(self.client, runtime, (404,))

    def test_login_http_rejects_a_foreign_runtime(self):
        runtime = self.runtime()
        ProviderRuntimeAccount.objects.filter(pk=runtime.pk).update(owner=self.other)
        self.assert_login_denied_without_proxy_contact(self.client, runtime, (404,))

    def complete(self, engine, upstream):
        runtime = self.runtime(engine, upstream)
        callback = self.login(runtime)
        runtime_api.relay_codex_oauth_callback(callback_url=callback)
        runtime.refresh_from_db()
        self.assertEqual(runtime.status, ProviderRuntimeAccount.STATUS_ACTIVE)
        source = runtime.source_provider_account
        self.assertEqual(source.login_status, ProviderAccount.LOGIN_ACTIVE)
        self.assertIsNotNone(source.last_login_at)
        offer = runtime.model_offers.get(upstream_model_id="personal-model")
        self.assertEqual(offer.health_status, "healthy")
        self.assertIsNone(runtime.provider_account)
        self.assertFalse(runtime.sources.exists())
        self.assertEqual(len(self.oauth_calls), 2)
        self.assertTrue(all(call[2] for call in self.oauth_calls))
        self.assertIn(("GET", "/v1/models"), self.calls)
        self.assertIn(("POST", "/v1/chat/completions", "personal-model"), self.calls)
        before = list(self.oauth_calls), list(self.calls)
        with self.assertRaisesRegex(runtime_api.ProviderRuntimeError, "unknown or expired"):
            runtime_api.relay_codex_oauth_callback(callback_url=callback)
        self.assertEqual((self.oauth_calls, self.calls), before)

    def test_codex_redirect_callback_and_catalog_with_real_http(self):
        self.complete("codex_proxy", "openai")

    def test_cliproxyapi_openai_callback_and_catalog_with_real_http(self):
        self.complete("cliproxyapi", "openai")

    def test_cliproxyapi_claude_callback_and_catalog_with_real_http(self):
        self.complete("cliproxyapi", "claude")

    def test_missing_authorization_url_never_registers_a_callback_state(self):
        runtime = self.runtime()
        type(self).oauth_mode = "missing_url"
        with patch("apps.providers.runtime_services.start_codex_oauth_callback_relay") as listener:
            with self.assertRaisesRegex(runtime_api.ProviderRuntimeError, "authorization URL"):
                runtime_api.provider_runtime_browser_login_url(runtime=runtime)
            listener.assert_not_called()
        self.assertNotIn(type(self).oauth_state, runtime_api._OAUTH_RELAY_STATES)
        runtime.refresh_from_db()
        self.assertEqual(runtime.status, ProviderRuntimeAccount.STATUS_LOGIN_REQUIRED)

    def test_rejected_callback_never_activates_or_discovers_models(self):
        runtime = self.runtime()
        callback = self.login(runtime)
        type(self).oauth_mode = "rejected"
        with self.assertRaisesRegex(runtime_api.ProviderRuntimeError, "callback relay failed"):
            runtime_api.relay_codex_oauth_callback(callback_url=callback)
        runtime.refresh_from_db()
        self.assertEqual(runtime.status, ProviderRuntimeAccount.STATUS_LOGIN_REQUIRED)
        self.assertEqual(ProviderAccount.objects.get(pk=runtime.source_provider_account_id).login_status,
                         ProviderAccount.LOGIN_REQUIRED)
        self.assertFalse(runtime.model_offers.exists())
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.oauth_calls), 2)
        with self.assertRaisesRegex(runtime_api.ProviderRuntimeError, "unknown or expired"):
            runtime_api.relay_codex_oauth_callback(callback_url=callback)
        self.assertEqual(len(self.oauth_calls), 2)

    def reject_changed_scope(self, changes):
        runtime = self.runtime()
        callback = self.login(runtime)
        ProviderRuntimeAccount.objects.filter(pk=runtime.pk).update(**changes)
        before = ProviderRuntimeAccount.objects.values().get(pk=runtime.pk)
        with self.assertRaises(ProviderRuntimeAccount.DoesNotExist):
            runtime_api.relay_codex_oauth_callback(callback_url=callback)
        self.assertEqual(ProviderRuntimeAccount.objects.values().get(pk=runtime.pk), before)
        self.assertEqual(len(self.oauth_calls), 1)
        self.assertEqual(self.calls, [])
        self.assertFalse(runtime.model_offers.exists())
        self.assertEqual(ProviderAccount.objects.get(pk=runtime.source_provider_account_id).login_status,
                         ProviderAccount.LOGIN_REQUIRED)

    def test_callback_rechecks_owner_before_contacting_proxy(self):
        self.reject_changed_scope({"owner": self.other})

    def test_callback_rechecks_project_before_contacting_proxy(self):
        project = Project.objects.create(tenant=self.installation.tenant, name="Changed project")
        self.reject_changed_scope({"project": project})

    def test_callback_rechecks_instance_before_contacting_proxy(self):
        tenant = Tenant.objects.create(name="Changed instance", slug="changed-oauth-instance")
        self.reject_changed_scope({"tenant": tenant})

    def test_callback_rejects_disabled_owner_without_remote_effects(self):
        runtime = self.runtime()
        callback = self.login(runtime)
        get_user_model().objects.filter(pk=self.installation.owner.pk).update(is_active=False)
        with self.assertRaises(ImproperlyConfigured):
            runtime_api.relay_codex_oauth_callback(callback_url=callback)
        self.assertEqual(len(self.oauth_calls), 1)
        self.assertEqual(self.calls, [])
        runtime.refresh_from_db()
        self.assertEqual(runtime.status, ProviderRuntimeAccount.STATUS_LOGIN_REQUIRED)
        self.assertFalse(runtime.model_offers.exists())
        self.assertEqual(ProviderAccount.objects.get(pk=runtime.source_provider_account_id).login_status,
                         ProviderAccount.LOGIN_REQUIRED)
