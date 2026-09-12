"""Authenticated Source/Pool APIs over real Provider discovery and health HTTP."""
import json
from uuid import uuid4
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.deployments.models import Deployment, ModelGroup
from apps.providers import runtime_services
from apps.providers.models import ProviderRuntimeAccount
from .provider_http_fixture import ProviderHTTPFixture


class PersonalDeploymentHTTPFixture(ProviderHTTPFixture):
    def setUp(self):
        super().setUp()
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.installation.owner)
        self.assertEqual(self.client.get("/api/v1/public/bootstrap/").status_code, 200)
        self.headers = {"HTTP_X_CSRFTOKEN": self.client.cookies["csrftoken"].value, "HTTP_ORIGIN": "http://testserver"}

    def post(self, path, data=None):
        return self.client.post(path, data or {}, format="json", **self.headers)

    def patch(self, path, data):
        return self.client.patch(path, data, format="json", **self.headers)

    def runtime(self, name="upstream"):
        _, runtime = self.create(name)
        runtime_services.start_provider_runtime(request=self.request(), runtime_id=str(runtime.pk))
        return runtime, runtime.model_offers.get()

    def source(self):
        created = self.post("/api/v1/provider-connections/", {"name": "HTTP source provider", "engine": "direct_api",
            "url": f"http://127.0.0.1:{self.upstream.server_port}/v1", "key": "local-provider-test-key"})
        self.assertEqual(created.status_code, 201, created.data)
        provider = self.post(f"/api/v1/provider-connections/{created.data['id']}/start/")
        self.assertEqual(provider.status_code, 200, provider.data)
        runtime = ProviderRuntimeAccount.objects.get(source_provider_account_id=created.data["id"])
        response = self.post("/api/v1/deployments/", {"source_type": "provider_runtime",
            "provider_runtime_id": str(runtime.pk), "model_offer_id": provider.data["models"][0]["id"],
            "deployment_id": "api-source", "model_group_name": "api-pool"})
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def assert_safe(self, value):
        text = json.dumps(value, default=str)
        for secret in ("local-provider-test-key", "encrypted_key", "Authorization", "marketplace_contribution", "pool_contribution"):
            self.assertNotIn(secret, text)


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1")
class PersonalDeploymentHTTPTests(PersonalDeploymentHTTPFixture, TestCase):
    def test_full_http_create_health_routing_history_rollback_and_delete(self):
        source = self.source()
        self.assertEqual(source["visibility"], "private")
        self.assertEqual(source["ownership"]["project_id"], str(self.installation.project_id))
        self.assert_safe(source)
        listing = self.client.get("/api/v1/deployments/")
        self.assertEqual(listing.status_code, 200, listing.data)
        self.assertEqual([row["id"] for row in listing.data], [source["id"]])
        pools = self.client.get("/api/v1/models/")
        self.assertEqual(pools.status_code, 200, pools.data)
        group = pools.data[0]
        self.assertEqual(group["deployments"][0]["deployment"]["id"], source["id"])
        self.assert_safe(pools.data)
        path = f"/api/v1/models/{group['id']}/routing/"
        revision = group["routing_revision"]
        updated = self.patch(path, {"expected_revision": revision, "routing_strategy": "weighted",
            "sources": [{"id": group["deployments"][0]["id"], "weight": 9}]})
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data["routing_revision"], revision + 1)
        self.assertEqual(self.patch(path, {"expected_revision": revision, "routing_strategy": "fallback"}).status_code, 409)
        history = self.client.get(path + "history/")
        self.assertEqual(history.status_code, 200, history.data)
        self.assertEqual(len(history.data), 2)
        restored = self.post(path + f"history/{revision}/rollback/", {"expected_revision": revision + 1})
        self.assertEqual(restored.status_code, 200, restored.data)
        self.assertEqual(restored.data["routing_strategy"], "fallback")
        type(self).calls.clear()
        source_path = f"/api/v1/deployments/{source['id']}/"
        health = self.post(source_path + "health-check/")
        self.assertEqual(health.status_code, 200, health.data)
        self.assertEqual(health.data["health_status"], "healthy")
        self.assertEqual(self.calls, [("GET", "/v1/models")])
        self.assertEqual(self.post(source_path + "disable/").status_code, 200)
        status = self.client.get("/api/v1/deployments/status/")
        self.assertEqual(status.status_code, 200, status.data)
        self.assertEqual(status.data[0]["status"], "disabled")
        deleted = self.client.delete(source_path, **self.headers)
        self.assertEqual(deleted.status_code, 200, deleted.data)
        self.assertEqual(set(deleted.data), {"id", "deployment_id", "status"})
        self.assertEqual(deleted.data["status"], "deleted")
        self.assertEqual(self.client.get("/api/v1/deployments/").data, [])
        self.assertEqual(self.client.get("/api/v1/models/").data[0]["deployments"], [])

    def test_batch_validation_and_transaction_rollback(self):
        runtime, offer = self.runtime()
        origin = {"type": "provider_runtime", "provider_runtime_id": str(runtime.pk)}
        item = {"source_id": "batch-source", "model_offer_id": str(offer.pk), "new_pool": {"name": "batch-pool"}}
        response = self.post("/api/v1/model-sources/batch/", {"origin": origin, "sources": [item, item]})
        self.assertEqual(response.status_code, 400, response.data)
        invalid = {**item, "source_id": "missing", "model_offer_id": str(uuid4())}
        response = self.post("/api/v1/model-sources/batch/", {"origin": origin, "sources": [item, invalid]})
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Deployment.objects.exists())
        self.assertFalse(ModelGroup.objects.exists())
        response = self.post("/api/v1/model-sources/batch/", {"origin": origin, "sources": [item]})
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data[0]["deployment_id"], "batch-source")
        self.assert_safe(response.data)

    def test_session_csrf_and_foreign_context_never_grant_access(self):
        source = self.source()
        path = f"/api/v1/deployments/{source['id']}/"
        self.assertEqual(self.client.delete(path).status_code, 403)
        anonymous = APIClient()
        self.assertIn(anonymous.get("/api/v1/models/").status_code, (401, 403))
        foreign = APIClient()
        foreign.force_login(self.other)
        self.assertIn(foreign.get("/api/v1/deployments/").status_code, (401, 403))
        self.assertEqual(self.client.get("/api/v1/deployments/", HTTP_X_NEXUS_PROJECT=str(uuid4())).status_code, 403)
        self.assertNotEqual(Deployment.objects.get(pk=source["id"]).status, "deleted")

    def test_pool_nested_sources_filter_changed_credential_ownership(self):
        source = self.source()
        deployment = Deployment.objects.get(pk=source["id"])
        account = deployment.provider_account
        account.created_by = self.other
        account.save(update_fields=["created_by"])
        self.assertEqual(self.client.get("/api/v1/deployments/").data, [])
        pools = self.client.get("/api/v1/models/")
        self.assertEqual(pools.status_code, 200, pools.data)
        self.assertEqual(pools.data[0]["deployments"], [])
        self.assertEqual(self.post(f"/api/v1/deployments/{source['id']}/health-check/").status_code, 404)

    def test_unknown_origins_sharing_and_mutable_source_identity_are_rejected(self):
        runtime, offer = self.runtime()
        base = {"source_type": "provider_runtime", "provider_runtime_id": str(runtime.pk), "model_offer_id": str(offer.pk)}
        for patch in ({"source_type": "marketplace"}, {"visibility": "public"}, {"pool_contribution_id": str(uuid4())},
                      {"endpoint": "https://other.example/v1"}, {"model_group_id": str(uuid4()), "model_group_name": "also-name"}):
            response = self.post("/api/v1/deployments/", {**base, **patch})
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Deployment.objects.exists())
        source = self.source()
        path = f"/api/v1/deployments/{source['id']}/"
        self.assertEqual(self.patch(path, {"endpoint": "https://other.example/v1"}).status_code, 409)
        self.assertEqual(self.post(path + "visibility/", {"visibility": "public"}).status_code, 409)
        self.assertEqual(self.post(path + "pricing/", {"pricing_rate": "1.0"}).status_code, 409)

    def test_single_source_policy_response_has_caller_context(self):
        source = self.source()
        group = self.client.get("/api/v1/models/").data[0]
        link = group["deployments"][0]
        response = self.patch(f"/api/v1/models/{group['id']}/sources/{link['id']}/", {
            "expected_revision": group["routing_revision"], "priority": 23})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["priority"], 23)
        self.assertEqual(response.data["deployment"]["id"], source["id"])
        self.assert_safe(response.data)
