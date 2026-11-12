from django.test import TestCase, override_settings
from apps.providers.models import ProviderRuntimeAccount, ProviderRuntimeModelOffer
from apps.providers.runtime_services import refresh_runtime_model_offers
from apps.routers import services as routers
from apps.deployments.models import ModelGroup
from .test_deployment_http import PersonalDeploymentHTTPFixture


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1")
class ExplicitModelTests(PersonalDeploymentHTTPFixture, TestCase):
    def setUp(self):
        super().setUp()
        result = self.post("/api/v1/provider-connections/", {"name": "Native", "engine": "direct_api", "upstream_provider": "system_one",
            "url": f"http://127.0.0.1:{self.upstream.server_port}/v1", "key": "local-provider-test-key"})
        self.assertEqual(result.status_code, 201, result.data)
        self.account = result.data["id"]
        self.post(f"/api/v1/provider-connections/{self.account}/start/")
        self.runtime_row = ProviderRuntimeAccount.objects.get(source_provider_account_id=self.account)
        type(self).calls.clear()

    def add(self, model="decision-model-preview"):
        result = self.post(f"/api/v1/provider-connections/{self.account}/models/explicit/", {"upstream_model_id": model})
        self.assertEqual(result.status_code, 200, result.data)
        return self.runtime_row.model_offers.get(upstream_model_id=model)

    def test_explicit_model_survives_catalog_refresh_and_router_can_select_it(self):
        offer = self.add()
        self.assertEqual(offer.metadata["discovery_source"], "explicit")
        self.assertEqual(offer.health_status, "degraded")
        self.assertIsNone(offer.last_discovered_at)
        self.assertEqual(self.calls, [])
        refresh_runtime_model_offers(runtime=self.runtime_row, actor=self.installation.owner, strict=True)
        offer.refresh_from_db()
        self.assertEqual(offer.status, "detected")
        self.assertEqual(offer.health_status, "degraded")
        one = self.post(f"/api/v1/provider-connections/{self.account}/models/{offer.pk}/refresh/")
        self.assertEqual(one.status_code, 200, one.data)
        offer.refresh_from_db(); self.assertEqual(offer.status, "detected")
        self.assertFalse(any(row[0] == "POST" for row in self.calls))
        # No per-Router text override is needed; ordinary catalog selection
        # accepts the operator-declared Offer from the chosen Provider.
        router = routers.create_router(request=self.request(), name="Explicit decision", strategy="system_one",
            decision_config={"provider_connection_id": self.account, "model": offer.upstream_model_id})
        self.assertEqual(router.decision_config["model"], offer.upstream_model_id)

    def test_explicit_model_can_be_used_as_a_source_and_does_not_revive_disabled(self):
        offer = self.add()
        result = self.post("/api/v1/deployments/", {"source_type": "provider_runtime", "provider_runtime_id": str(self.runtime_row.pk),
            "model_offer_id": str(offer.pk), "deployment_id": "native-decision", "model_group_name": "decision-pool"})
        self.assertEqual(result.status_code, 201, result.data)
        self.assertTrue(ModelGroup.objects.filter(name="decision-pool").exists())
        offer.status = "disabled"; offer.save()
        result = self.post(f"/api/v1/provider-connections/{self.account}/models/explicit/", {"upstream_model_id": offer.upstream_model_id})
        self.assertEqual(result.status_code, 400)
        self.runtime_row.refresh_from_db()
        refresh_runtime_model_offers(runtime=self.runtime_row, actor=self.installation.owner, strict=True)
        offer.refresh_from_db(); self.assertEqual(offer.status, "disabled")

    def test_discovered_model_still_becomes_unavailable_and_foreign_user_cannot_add(self):
        discovered = self.runtime_row.model_offers.get()
        discovered.upstream_model_id = "removed-model"; discovered.save()
        self.add()
        refresh_runtime_model_offers(runtime=self.runtime_row, actor=self.installation.owner, strict=True)
        discovered.refresh_from_db(); self.assertEqual(discovered.status, "unavailable")
        self.client.force_login(self.other)
        result = self.post(f"/api/v1/provider-connections/{self.account}/models/explicit/", {"upstream_model_id": "denied"})
        self.assertIn(result.status_code, (401, 403, 404))
        self.assertFalse(ProviderRuntimeModelOffer.objects.filter(upstream_model_id="denied").exists())

    def native_client(self):
        offer = self.add()
        result = self.post("/api/v1/deployments/", {"source_type": "provider_runtime", "provider_runtime_id": str(self.runtime_row.pk),
            "model_offer_id": str(offer.pk), "deployment_id": "native-decision", "model_group_name": "decision-pool"})
        self.assertEqual(result.status_code, 201, result.data)
        router = routers.create_router(request=self.request(), name="Native", model_group_ids=[ModelGroup.objects.get(name="decision-pool").pk])
        routers.deploy_router(request=self.request(), router_id=str(router.pk))
        access = self.post(f"/api/v1/routers/{router.pk}/api-access/", {"action": "ensure"})
        self.assertEqual(access.status_code, 200, access.data)
        self.assertEqual(access.data["credentials"]["systemone"]["available_models"], [router.outputs.get().model_name])
        from rest_framework.test import APIClient
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION="Bearer " + access.data["credentials"]["gateway_api_key"])
        type(self).system_one_choice = "pool_0"
        payload = {"model": router.outputs.get().model_name,
            "state": "Isolated test", "questions": {"route": {"type": "choice", "instructions": "Choose", "criteria": {"pool_0": "A", "pool_1": "B"}}}}
        return client, payload, router, offer, access.data["credentials"]["gateway_api_key_id"]

    def test_native_endpoint_accepts_scoped_personal_router_key(self):
        client, payload, router, offer, _ = self.native_client()
        result = client.post("/api/v1/openai/v1/systemone", payload, format="json")
        self.assertEqual(result.status_code, 200, result.data)
        from apps.gateway.models import GatewayRequestLog
        self.assertEqual(GatewayRequestLog.objects.get(status="success").operation, "systemone")
        offer.refresh_from_db(); self.assertEqual(offer.health_status, "healthy")

    def test_native_request_id_audit_and_repeat_never_dispatch_twice(self):
        client, payload, _, _, _ = self.native_client()
        payload["state"] = "private-decision-state-must-not-be-logged"
        headers = {"HTTP_IDEMPOTENCY_KEY": "community-decision-1", "HTTP_X_REQUEST_ID": "community-native-1"}
        first = client.post("/api/v1/openai/v1/systemone", payload, format="json", **headers)
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first["X-Request-ID"], "community-native-1")
        self.assertEqual(first["Cache-Control"], "no-store")
        second = client.post("/api/v1/openai/v1/systemone", payload, format="json", **headers)
        self.assertEqual(second.status_code, 409, second.data)
        self.assertEqual(len(self.system_one_payloads), 1)
        from apps.gateway.models import GatewayDecisionOperation, GatewayRequestLog
        self.assertEqual(GatewayDecisionOperation.objects.get().state, "succeeded")
        log = GatewayRequestLog.objects.get(status="success")
        self.assertEqual(log.request_id, "community-native-1")
        self.assertNotIn(payload["state"], str(log.__dict__))

    def test_native_credentials_reject_other_router_and_revocation_before_dispatch(self):
        client, payload, _, _, key_id = self.native_client()
        from uuid import uuid4
        rejected = client.post("/api/v1/openai/v1/systemone", {**payload, "router_id": str(uuid4())}, format="json")
        self.assertEqual(rejected.status_code, 403, rejected.data)
        from nexus_personal.models import PersonalRouterCredential
        from django.utils import timezone
        PersonalRouterCredential.objects.filter(pk=key_id).update(revoked_at=timezone.now())
        revoked = client.post("/api/v1/openai/v1/systemone", payload, format="json")
        self.assertIn(revoked.status_code, (401, 403))
        self.assertEqual(self.system_one_payloads, [])

    def test_native_invalid_and_unauthenticated_requests_never_reach_upstream(self):
        client, payload, _, _, _ = self.native_client()
        from rest_framework.test import APIClient
        denied = APIClient().post("/api/v1/openai/v1/systemone", payload, format="json")
        self.assertIn(denied.status_code, (401, 403))
        for extra in ({"stream": True}, {"endpoint": "https://example.invalid"}, {"model": "unpublished"}):
            result = client.post("/api/v1/openai/v1/systemone", {**payload, **extra}, format="json")
            self.assertIn(result.status_code, (400, 403), result.data)
        duplicate = client.post("/api/v1/openai/v1/systemone", '{"model":"a","model":"b"}', content_type="application/json")
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(self.system_one_payloads, [])
