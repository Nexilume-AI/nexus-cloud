"""Owner HTTP -> shared Provider services -> actual HTTP upstream, no private imports."""
import json
from uuid import uuid4
from unittest.mock import patch
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.common.crypto import decrypt_secret
from apps.audit.models import AuditLog
from apps.deployments.models import Deployment, ModelGroup, ModelGroupDeployment
from apps.providers.models import ProviderAccount, ProviderRuntimeAccount
from apps.providers.runtime_runner import ProviderRuntimeStartResult
from .provider_http_fixture import ProviderHTTPFixture


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1")
class PersonalProviderHTTPTests(ProviderHTTPFixture, TestCase):
    base = "/api/v1/provider-connections/"

    def setUp(self):
        super().setUp()
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.installation.owner)
        response = self.client.get("/api/v1/public/bootstrap/")
        self.assertEqual(response.status_code, 200)
        self.headers = {"HTTP_X_CSRFTOKEN": self.client.cookies["csrftoken"].value,
                        "HTTP_ORIGIN": "http://testserver"}

    def post(self, path, data=None):
        return self.client.post(path, data or {}, format="json", **self.headers)

    def create_http(self, name="Personal upstream"):
        response = self.post(self.base, {"name": name, "account_id": name,
            "engine": "direct_api", "url": f"http://127.0.0.1:{self.upstream.server_port}/v1",
            "key": "local-provider-test-key"})
        self.assertEqual(response.status_code, 201, response.data)
        self.assert_safe(response.data)
        return response.data["id"], self.base + response.data["id"] + "/"

    def assert_safe(self, data):
        content = json.dumps(data, default=str)
        for forbidden in ("local-provider-test-key", "encrypted_key", "Authorization", "publication", "published_count", "pool_contribution"):
            self.assertNotIn(forbidden, content)

    def create_proxy(self, engine="cliproxyapi", upstream="openai"):
        name = f"{engine}-{upstream}"
        response = self.post(self.base, {"name": name, "account_id": name,
                                       "engine": engine, "upstream_provider": upstream})
        self.assertEqual(response.status_code, 201, response.data)
        self.assert_safe(response.data)
        account = ProviderAccount.objects.get(pk=response.data["id"])
        runtime = ProviderRuntimeAccount.objects.get(source_provider_account=account)
        return account, runtime, self.base + str(account.pk) + "/"

    def test_proxy_creation_binds_one_runtime_without_starting_or_creating_sources(self):
        with patch("apps.providers.runtime_services.get_provider_runtime_runner") as runner:
            for engine, upstream in (("codex_proxy", "openai"), ("cliproxyapi", "openai"),
                                     ("cliproxyapi", "claude")):
                with self.subTest(engine=engine, upstream=upstream):
                    account, runtime, path = self.create_proxy(engine, upstream)
                    self.assertEqual(account.provider.name, upstream)
                    self.assertEqual(account.auth_mode, ProviderAccount.AUTH_INTERACTIVE_LOGIN)
                    self.assertEqual(account.encrypted_username, "")
                    self.assertEqual(account.encrypted_password, "")
                    self.assertEqual(runtime.runtime_type, engine)
                    self.assertEqual(runtime.owner_id, self.installation.owner_id)
                    self.assertEqual(runtime.project_id, self.installation.project_id)
                    self.assertEqual(runtime.status, ProviderRuntimeAccount.STATUS_CREATED)
                    self.assertEqual(runtime.container_id, "")
                    self.assertEqual(runtime.encrypted_proxy_api_key, "")
                    self.assertFalse(runtime.model_offers.exists())
                    self.assertFalse(runtime.sources.exists())
                    # Repair of a healthy binding must not create a second runtime.
                    repaired = self.post(path + "repair/")
                    self.assertEqual(repaired.status_code, 200, repaired.data)
                    self.assertEqual(account.source_runtime_accounts.count(), 1)
                    self.assertEqual(account.source_runtime_accounts.get().pk, runtime.pk)
            runner.assert_not_called()
        self.assertEqual(self.calls, [])
        self.assertFalse(Deployment.objects.exists())
        self.assertFalse(ModelGroup.objects.exists())

    def test_proxy_start_waits_for_login_and_does_not_advertise_unverified_models(self):
        account, runtime, path = self.create_proxy()
        base = f"http://127.0.0.1:{self.upstream.server_port}"
        # Only the process controller is stubbed. ORM, owner HTTP admission,
        # credential generation and lifecycle services run unchanged.
        with patch("apps.providers.runtime_services.get_provider_runtime_runner") as runner:
            runner.return_value.start.return_value = ProviderRuntimeStartResult(
                "controller-test-container", base + "/auth/login", base + "/v1")
            response = self.post(path + "start/")
            self.assertEqual(response.status_code, 200, response.data)
            runner.return_value.start.assert_called_once()
            dispatched = runner.return_value.start.call_args.kwargs
            self.assertEqual(dispatched["runtime"].pk, runtime.pk)
            self.assertTrue(dispatched["proxy_api_key"])
            repeated = self.post(path + "start/")
            self.assertEqual(repeated.status_code, 200, repeated.data)
            runner.return_value.start.assert_called_once()
            runner.return_value.login_with_credentials.assert_not_called()
        account.refresh_from_db()
        runtime.refresh_from_db()
        self.assertEqual(runtime.status, ProviderRuntimeAccount.STATUS_LOGIN_REQUIRED)
        self.assertEqual(account.login_status, ProviderAccount.LOGIN_REQUIRED)
        self.assertEqual(decrypt_secret(runtime.encrypted_proxy_api_key), dispatched["proxy_api_key"])
        self.assertNotIn(dispatched["proxy_api_key"], response.content.decode())
        self.assertFalse(runtime.model_offers.exists())
        self.assertFalse(runtime.sources.exists())
        self.assertIsNone(runtime.provider_account_id)
        self.assertEqual(self.calls, [])
        self.assertTrue(AuditLog.objects.filter(action="providers.runtime.start", resource_id=str(runtime.pk)).exists())

    def test_proxy_start_failure_is_persisted_and_retry_reuses_credential(self):
        account, runtime, path = self.create_proxy()
        with patch("apps.providers.runtime_services.get_provider_runtime_runner") as runner:
            runner.return_value.start.side_effect = RuntimeError("Provider Runtime image is unavailable.")
            response = self.post(path + "start/")
            self.assertEqual(response.status_code, 400, response.data)
            runtime.refresh_from_db()
            self.assertEqual(runtime.status, ProviderRuntimeAccount.STATUS_FAILED)
            self.assertEqual(runtime.last_error, "Provider Runtime image is unavailable.")
            secret = runner.return_value.start.call_args.kwargs["proxy_api_key"]
            self.assertNotIn(secret, response.content.decode())
            self.assertTrue(AuditLog.objects.filter(action="providers.runtime.start.failed", resource_id=str(runtime.pk)).exists())
            runner.return_value.start.side_effect = None
            base = f"http://127.0.0.1:{self.upstream.server_port}"
            runner.return_value.start.return_value = ProviderRuntimeStartResult(
                "controller-retry-container", base + "/auth/login", base + "/v1")
            recovered = self.post(path + "start/")
            self.assertEqual(recovered.status_code, 200, recovered.data)
            self.assertEqual(runner.return_value.start.call_count, 2)
            self.assertEqual(runner.return_value.start.call_args.kwargs["proxy_api_key"], secret)
        runtime.refresh_from_db()
        self.assertEqual(runtime.status, ProviderRuntimeAccount.STATUS_LOGIN_REQUIRED)
        self.assertFalse(runtime.model_offers.exists())
        self.assertFalse(runtime.sources.exists())
        self.assertEqual(self.calls, [])
        self.assertNotIn(secret, json.dumps(list(AuditLog.objects.values_list("metadata", flat=True))))

    def test_http_lifecycle_catalog_refresh_and_safe_removal(self):
        pk, path = self.create_http()
        response = self.post(path + "start/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "active")
        self.assertEqual(response.data["model_count"], 1)
        offer = response.data["models"][0]
        self.assertEqual(offer["upstream_model_id"], "personal-model")
        self.assert_safe(response.data)
        self.assertIn(("POST", "/v1/chat/completions", "personal-model"), self.calls)
        summary = self.client.get(self.base, {"limit": 1, "projection": "summary", "q": "Personal upstream"})
        self.assertEqual(summary.status_code, 200, summary.data)
        self.assertEqual(summary.data["items"][0]["model_count"], 1)
        self.assertFalse(summary.data["items"][0]["models_loaded"])
        self.assertEqual(summary.data["items"][0]["models"], [])
        self.assert_safe(summary.data)
        for suffix in ("models/refresh/", f"models/{offer['id']}/refresh/", "health/"):
            response = self.post(path + suffix)
            self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.post(path + "models/refresh/", {"key": "not-accepted"}).status_code, 400)
        update = self.client.patch(path, {"name": "Renamed upstream"}, format="json", **self.headers)
        self.assertEqual(update.status_code, 200, update.data)
        self.assertEqual(update.data["name"], "Renamed upstream")
        impact = self.client.get(path + "deletion-impact/")
        self.assertEqual(impact.status_code, 200, impact.data)
        self.assertEqual(impact.data["model_count"], 1)
        self.assertTrue(impact.data["requires_name_confirmation"])
        self.assert_safe(impact.data)
        self.assertEqual(self.post(path + "remove/", {"confirmation_name": "wrong"}).status_code, 400)
        self.assertEqual(self.post(path + "remove/", {"confirmation_name": "Renamed upstream"}).status_code, 200)
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.post(path + "remove/").status_code, 200)
        account = ProviderAccount.objects.get(pk=pk)
        self.assertEqual(decrypt_secret(account.encrypted_key), "")
        runtime = ProviderRuntimeAccount.objects.get(source_provider_account_id=pk)
        self.assertEqual(runtime.status, "deleted")
        self.assertEqual(runtime.encrypted_proxy_api_key, "")

    def test_csrf_identity_context_and_commercial_routes_are_closed(self):
        pk, path = self.create_http()
        self.assertEqual(self.client.post(path + "start/", {}, format="json").status_code, 403)
        self.assertEqual(self.calls, [])
        anonymous = APIClient()
        self.assertIn(anonymous.get(self.base).status_code, (401, 403))
        foreign = APIClient()
        foreign.force_login(self.other)
        self.assertIn(foreign.get(path).status_code, (401, 403))
        self.assertIn(self.client.get(path, HTTP_X_NEXUS_TENANT=str(uuid4())).status_code, (400, 403))
        for suffix in ("share/", "unshare/"):
            self.assertEqual(self.post(path + suffix).status_code, 404)
        self.assertEqual(self.client.get("/api/v1/provider-pool/").status_code, 404)
        account = ProviderAccount.objects.get(pk=pk)
        account.created_by = self.other
        account.save(update_fields=["created_by"])
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.post(path + "remove/").status_code, 404)

    def test_paginated_search_cursor_stays_owner_scoped(self):
        self.create_http("First")
        self.create_http("Second")
        first = self.client.get(self.base, {"limit": 1, "projection": "summary"})
        self.assertEqual(first.status_code, 200, first.data)
        self.assertTrue(first.data["has_more"])
        cursor = first.data["next_cursor"]
        second = self.client.get(self.base, {"limit": 1, "projection": "summary", "cursor": cursor})
        self.assertEqual(second.status_code, 200, second.data)
        self.assertNotEqual(first.data["items"][0]["id"], second.data["items"][0]["id"])
        self.assertEqual(self.client.get(self.base, {"limit": 1, "cursor": cursor, "q": "changed"}).status_code, 400)
        self.assertEqual(self.client.get(self.base, {"limit": 10, "q": "no-match"}).data["items"], [])

    def test_active_engine_change_is_rejected_without_partial_update(self):
        pk, path = self.create_http()
        self.assertEqual(self.post(path + "start/").status_code, 200)
        response = self.client.patch(path, {"engine": "cliproxyapi", "upstream_provider": "claude"}, format="json", **self.headers)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(ProviderRuntimeAccount.objects.get(source_provider_account_id=pk).runtime_type, "direct_api")
        self.assertEqual(self.post(path + "stop/").status_code, 200)
        self.assertEqual(self.post(path + "start/").status_code, 200)

    def test_source_impact_and_cleanup_leave_model_pool_and_other_provider_intact(self):
        pk, path = self.create_http()
        other_pk, other_path = self.create_http("Other provider")
        self.assertEqual(self.post(path + "start/").status_code, 200)
        runtime = ProviderRuntimeAccount.objects.get(source_provider_account_id=pk)
        offer = runtime.model_offers.get()
        source = Deployment.objects.create(tenant=self.installation.tenant, project=self.installation.project,
            provider=runtime.source_provider_account.provider, provider_account_id=pk, provider_runtime=runtime,
            runtime_model_offer=offer, canonical_model=offer.canonical_model, deployment_id="own-source",
            upstream_model_id=offer.upstream_model_id, created_by=self.installation.owner)
        group = ModelGroup.objects.create(tenant=self.installation.tenant, project=self.installation.project,
            name="own-pool", canonical_model=offer.canonical_model, created_by=self.installation.owner)
        link = ModelGroupDeployment.objects.create(model_group=group, deployment=source)
        detail = self.client.get(path)
        self.assertEqual(detail.data["source_count"], 1)
        self.assertEqual(detail.data["models"][0]["source_ids"], [str(source.pk)])
        self.assertEqual(detail.data["technical_details"]["runtime_id"], str(runtime.pk))
        summary = self.client.get(self.base, {"limit": 1, "q": "Personal upstream", "projection": "summary"})
        self.assertEqual(summary.data["items"][0]["source_count"], 1)
        impact = self.client.get(path + "deletion-impact/")
        self.assertEqual(impact.data["source_count"], 1)
        self.assertEqual(self.post(path + "remove/", {"confirmation_name": "Personal upstream"}).status_code, 200)
        source.refresh_from_db()
        link.refresh_from_db()
        group.refresh_from_db()
        self.assertEqual(source.status, "deleted")
        self.assertEqual(link.status, "deleted")
        self.assertNotEqual(group.status, "deleted")
        self.assertEqual(self.client.get(other_path).status_code, 200)
        self.assertNotEqual(ProviderAccount.objects.get(pk=other_pk).status, "deleted")
        event = AuditLog.objects.get(action="providers.connection.remove", resource_id=pk)
        self.assertEqual(event.metadata["source_count"], 1)
        self.assertNotIn("published_count", event.metadata)

    def test_invalid_catalog_recovery_through_http_keeps_model_identity(self):
        pk, path = self.create_http()
        first = self.post(path + "start/")
        self.assertEqual(first.status_code, 200, first.data)
        offer_id = first.data["models"][0]["id"]
        type(self).catalog_invalid = True
        failed = self.post(path + "models/refresh/")
        self.assertGreaterEqual(failed.status_code, 400)
        self.assertLess(failed.status_code, 600)
        detail = self.client.get(path)
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data["models"][0]["id"], offer_id)
        self.assert_safe(detail.data)
        type(self).catalog_invalid = False
        recovered = self.post(path + "models/refresh/")
        self.assertEqual(recovered.status_code, 200, recovered.data)
        self.assertEqual(recovered.data["models"][0]["id"], offer_id)
        self.assertEqual(recovered.data["models"][0]["health_status"], "healthy")

    def test_browser_wire_contract_creates_sources_without_database_id_lookup(self):
        pk, path = self.create_http()
        started = self.post(path + "start/").data
        offer = started["models"][0]
        self.assertEqual(offer["source_ids"], [])
        result = self.post("/api/v1/model-sources/batch/", {
            "origin": {"type": "provider_runtime", "provider_runtime_id": started["technical_details"]["runtime_id"]},
            "sources": [{"model_offer_id": offer["id"], "source_id": "browser-created-source",
                "visibility": "private", "new_pool": {"name": offer["canonical_model_key"], "visibility": "private"}}]})
        self.assertEqual(result.status_code, 201, result.data)
        detail = self.client.get(path).data
        source = Deployment.objects.get(deployment_id="browser-created-source")
        self.assertEqual(detail["models"][0]["source_ids"], [str(source.pk)])
        self.assertEqual(detail["source_count"], 1)
        self.assertNotIn("internal_api_url", detail["technical_details"])
        source.delete()
        self.assertEqual(self.client.get(path).data["models"][0]["source_ids"], [])
        self.assert_safe(detail)

    def test_personal_recovery_is_real_bounded_catalog_and_rejects_linked_cleanup(self):
        pk, path = self.create_http()
        linked = ProviderRuntimeAccount.objects.get(source_provider_account_id=pk)
        route = "/api/v1/provider-runtimes/"
        query = {"unlinked": "true", "limit": "1"}
        self.assertEqual(self.client.get(route, query).data["items"], [])
        legacy = ProviderRuntimeAccount.objects.create(tenant=self.installation.tenant,
            project=self.installation.project, owner=self.installation.owner, name="Unlinked local runtime",
            runtime_type="direct_api", internal_api_url="https://must-not-leak.invalid")
        listing = self.client.get(route, query)
        self.assertEqual(listing.status_code, 200, listing.data)
        self.assertEqual([row["id"] for row in listing.data["items"]], [str(legacy.pk)])
        self.assertNotIn("must-not-leak", str(listing.data))
        self.assertEqual(APIClient().get(route).status_code, 401)
        self.assertEqual(self.client.get(route, HTTP_X_NEXUS_PROJECT="foreign").status_code, 403)
        self.assertEqual(self.client.post(route, {}, format="json", **self.headers).status_code, 405)
        self.assertEqual(self.client.delete(route + str(linked.pk) + "/", **self.headers).status_code, 400)
        self.assertEqual(self.client.delete(route + str(legacy.pk) + "/").status_code, 403)
        self.assertEqual(self.client.delete(route + str(legacy.pk) + "/", **self.headers).status_code, 200)
        legacy.refresh_from_db()
        self.assertEqual(legacy.status, "deleted")
        self.assertEqual(self.client.get(route, query).data["items"], [])
