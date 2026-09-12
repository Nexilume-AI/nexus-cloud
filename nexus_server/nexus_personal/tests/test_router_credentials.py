"""Real scoped credential export -> model directory -> HTTP inference and revocation."""
from datetime import timedelta
import hashlib
from uuid import uuid4
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from apps.audit.models import AuditLog
from apps.routers import services as routers
from apps.routers.models import Router
from nexus_personal.models import PersonalRouterCredential, PersonalGatewayRequest
from .provider_http_fixture import ProviderHTTPFixture
from . import test_router_runtime as runtime_tests


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1")
class PersonalRouterCredentialTests(ProviderHTTPFixture, TestCase):
    source = runtime_tests.PersonalRouterRuntimeTests.source
    post = runtime_tests.PersonalRouterRuntimeTests.post
    runtime = runtime_tests.PersonalRouterRuntimeTests.runtime
    setUp = runtime_tests.PersonalRouterRuntimeTests.setUp

    def issue(self):
        response = self.post(f"/api/v1/routers/{self.router.pk}/export-credentials/", {})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response.data["available_models"], [self.model])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer " + response.data["gateway_api_key"])
        return response.data, client

    def call(self, client, key="credential-call", **extra):
        return client.post("/api/v1/openai/v1/chat/completions", {"model": self.model,
            "messages": [{"role": "user", "content": "scoped request"}], **extra}, format="json", HTTP_X_REQUEST_ID=key)

    def revoke(self, credential_id):
        return self.post(f"/api/v1/routers/{self.router.pk}/credentials/{credential_id}/revoke/", {})

    def test_one_time_export_has_only_a_hash_at_rest_and_lists_no_secret(self):
        data, client = self.issue()
        token = data["gateway_api_key"]
        row = PersonalRouterCredential.objects.get()
        self.assertEqual(row.token_hash, hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual(row.owner_id, self.installation.owner.pk)
        self.assertGreater(row.expires_at, timezone.now() + timedelta(days=89))
        listing = self.client.get(f"/api/v1/routers/{self.router.pk}/credentials/")
        self.assertEqual(listing.status_code, 200, listing.data)
        self.assertEqual(listing.data["items"][0]["status"], "active")
        self.assertNotIn("token_hash", str(listing.data))
        self.assertNotIn(token, str(listing.data))
        persisted = str(list(PersonalRouterCredential.objects.values())) + str(list(AuditLog.objects.values()))
        self.assertNotIn(token, persisted)
        self.assertEqual(client.get("/api/v1/openai/v1/models").data["data"][0]["id"], self.model)

    def test_default_router_and_all_supported_call_routes_reach_real_upstream(self):
        data, client = self.issue()
        response = self.call(client)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["choices"][0]["message"]["content"], "healthy")
        payloads = [
            ("/api/v1/openai/v1/responses", {"model": self.model, "input": "response input"}),
            ("/api/v1/claude/v1/messages", {"model": self.model, "messages": [{"role": "user", "content": "claude input"}], "max_tokens": 32}),
            (f"/api/v1/routers/{self.router.pk}/invoke/", {"model": self.model, "messages": [{"role": "user", "content": "invoke"}]}),
        ]
        for i, (path, body) in enumerate(payloads):
            result = client.post(path, body, format="json", HTTP_X_REQUEST_ID=f"surface-{i}")
            self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(set(PersonalGatewayRequest.objects.values_list("router_id", flat=True)), {self.router.pk})
        self.assertEqual(PersonalGatewayRequest.objects.filter(state="completed").count(), 4)
        self.assertEqual(len(self.calls), 4)
        row = PersonalRouterCredential.objects.get(pk=data["gateway_api_key_id"])
        self.assertIsNotNone(row.last_used_at)

    def test_scoped_x_api_key_header_uses_the_same_restrictions(self):
        data, _ = self.issue()
        client = APIClient()
        client.credentials(HTTP_X_API_KEY=data["gateway_api_key"])
        self.assertEqual(client.get("/api/v1/claude/v1/models").status_code, 200)
        response = client.post("/api/v1/claude/v1/messages", {"model": self.model,
            "messages": [{"role": "user", "content": "scoped Claude header"}], "max_tokens": 32}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(client.get("/api/v1/account/me/").status_code, 403)
        client.credentials(HTTP_X_API_KEY=data["gateway_api_key"], HTTP_AUTHORIZATION="Bearer " + data["gateway_api_key"])
        self.assertEqual(self.call(client).status_code, 401)
        self.assertEqual(len(self.calls), 1)

    def test_other_routers_sources_models_and_management_endpoints_are_refused(self):
        data, client = self.issue()
        other = routers.create_router(request=self.request(), name="other-router", model_group_ids=[self.group.pk])
        routers.deploy_router(request=self.request(), router_id=str(other.pk))
        self.assertEqual(self.call(client, router_id=str(other.pk)).status_code, 403)
        self.assertEqual(self.call(client, model=str(self.source_row.pk)).status_code, 403)
        self.assertEqual(self.call(client, model="not-exported").status_code, 403)
        self.assertEqual(client.get("/api/v1/openai/v1/models", {"router_id": str(other.pk)}).status_code, 403)
        self.assertEqual(client.post(f"/api/v1/routers/{other.pk}/invoke/", {"model": self.model}, format="json").status_code, 403)
        for path in ("/api/v1/account/me/", "/api/v1/providers/", f"/api/v1/routers/{self.router.pk}/credentials/",
                     f"/api/v1/routers/{self.router.pk}/traces/"):
            self.assertIn(client.get(path).status_code, (403, 404))
        self.assertEqual(client.post(f"/api/v1/routers/{self.router.pk}/export-credentials/", {}, format="json").status_code, 403)
        self.assertEqual(self.calls, [])

    def test_revoked_expired_and_tampered_credentials_fail_before_dispatch(self):
        data, client = self.issue()
        self.assertEqual(self.revoke(data["gateway_api_key_id"]).status_code, 204)
        self.assertEqual(self.revoke(data["gateway_api_key_id"]).status_code, 204)
        self.assertEqual(self.call(client).status_code, 401)
        next_data, next_client = self.issue()
        PersonalRouterCredential.objects.filter(pk=next_data["gateway_api_key_id"]).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.call(next_client).status_code, 401)
        bad = APIClient()
        bad.credentials(HTTP_AUTHORIZATION="Bearer " + data["gateway_api_key"][:-1] + "!")
        self.assertEqual(self.call(bad).status_code, 401)
        bad.credentials(HTTP_AUTHORIZATION="Bearer np-router-not-a-valid-token")
        self.assertEqual(self.call(bad).status_code, 401)
        self.assertEqual(self.calls, [])

    def test_new_credential_does_not_revoke_old_until_explicitly_requested(self):
        old, old_client = self.issue()
        new, new_client = self.issue()
        self.assertEqual(self.call(new_client, "new-token-works").status_code, 200)
        self.assertEqual(self.call(old_client, "old-token-still-works").status_code, 200)
        self.assertEqual(self.revoke(old["gateway_api_key_id"]).status_code, 204)
        self.assertEqual(self.call(old_client, "old-revoked").status_code, 401)
        self.assertEqual(self.call(new_client, "new-token-survives").status_code, 200)

    def test_current_router_ownership_and_context_not_cached(self):
        _, client = self.issue()
        Router.objects.filter(pk=self.router.pk).update(created_by=self.other)
        self.assertEqual(self.call(client).status_code, 404)
        Router.objects.filter(pk=self.router.pk).update(created_by=self.installation.owner)
        self.assertEqual(client.get("/api/v1/openai/v1/models", HTTP_X_NEXUS_PROJECT=str(uuid4())).status_code, 403)
        Router.objects.filter(pk=self.router.pk).update(status="disabled")
        self.assertEqual(self.call(client).status_code, 404)
        self.assertEqual(self.calls, [])

    def test_new_model_alias_does_not_expand_existing_credential(self):
        _, client = self.issue()
        self.router.outputs.update(model_name="new-alias")
        self.assertEqual(client.get("/api/v1/openai/v1/models").data["data"], [])
        self.assertEqual(self.call(client, model="new-alias").status_code, 403)
        self.model = "new-alias"
        _, renewed = self.issue()
        self.assertEqual(self.call(renewed).status_code, 200)

    def test_revoke_during_stream_interrupts_without_replay(self):
        data, client = self.issue()
        response = self.call(client, "revoked-stream", stream=True)
        chunks = iter(response.streaming_content)
        self.assertIn(b"hello", next(chunks))
        self.assertEqual(self.revoke(data["gateway_api_key_id"]).status_code, 204)
        body = b"".join(chunks).decode()
        self.assertIn("error", body)
        row = PersonalGatewayRequest.objects.get(request_id="revoked-stream")
        self.assertEqual(row.state, "interrupted")
        self.assertIsNone(row.result_log_id)
        self.assertEqual(len(self.calls), 1)

    def test_metadata_pagination_and_cursor_scope_without_hash_or_token(self):
        self.issue()
        template = PersonalRouterCredential.objects.get()
        PersonalRouterCredential.objects.bulk_create([PersonalRouterCredential(owner=template.owner,
            tenant=template.tenant, project=template.project, router=self.router, model_names=[self.model],
            token_hash=hashlib.sha256(str(i).encode()).hexdigest(), expires_at=template.expires_at) for i in range(101)])
        PersonalRouterCredential.objects.update(created_at=template.created_at)
        path = f"/api/v1/routers/{self.router.pk}/credentials/"
        first = self.client.get(path).data
        self.assertEqual(len(first["items"]), 100)
        second = self.client.get(path, {"cursor": first["next_cursor"]}).data
        self.assertEqual(len(second["items"]), 2)
        self.assertIsNone(second["next_cursor"])
        self.assertEqual(len({row["id"] for row in first["items"] + second["items"]}), 102)
        self.assertEqual(self.client.get(path, {"cursor": str(uuid4())}).status_code, 400)
        self.assertNotIn("token_hash", str(first))
