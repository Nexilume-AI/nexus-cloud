"""Native discovery → Router config → actual decision HTTP → callable chat Source."""
from unittest.mock import patch
from django.test import override_settings
from apps.providers.models import ProviderRuntimeAccount
from apps.deployments.models import ModelGroup, Deployment
from apps.routers.models import Router
from apps.routers import services
from apps.routers.decision_routing import auto_model_name
from apps.gateway import services as gateway
from apps.gateway.provider_clients import OpenAICompatibleClient
from .test_gateway_routing import PersonalGatewayRoutingTests


class NativeSystemOneRoutingTests(PersonalGatewayRoutingTests):
    def decision_provider(self):
        result = self.post("/api/v1/provider-connections/", {"name": "Native decision", "engine": "direct_api", "upstream_provider": "system_one",
            "url": f"http://127.0.0.1:{self.upstream.server_port}/v1/systemone", "key": "local-provider-test-key"})
        self.assertEqual(result.status_code, 201, result.data)
        started = self.post(f"/api/v1/provider-connections/{result.data['id']}/start/")
        self.assertEqual(started.status_code, 200, started.data)
        self.assertEqual(started.data["models"][0]["health_status"], "healthy")
        self.assertNotIn("POST", [call[0] for call in self.calls])
        listing = self.client.get("/api/v1/provider-connections/?protocol=system_one&limit=25&projection=summary")
        self.assertEqual(listing.status_code, 200, listing.data)
        self.assertEqual([row["id"] for row in listing.data["items"]], [result.data["id"]])
        return result.data["id"]

    def configure(self):
        account_id = self.decision_provider()
        runtime = ProviderRuntimeAccount.objects.exclude(source_provider_account_id=account_id).get()
        second = self.post("/api/v1/deployments/", {"source_type": "provider_runtime", "provider_runtime_id": str(runtime.pk),
            "model_offer_id": str(runtime.model_offers.get().pk), "deployment_id": "second-source", "model_group_name": "second-pool"})
        self.assertEqual(second.status_code, 201, second.data)
        second_group = ModelGroup.objects.get(name="second-pool")
        updated = self.client.patch(f"/api/v1/routers/{self.execution.pk}/", {"strategy": "system_one",
            "model_group_ids": [str(self.group.pk), str(second_group.pk)],
            "decision_config": {"provider_connection_id": str(account_id), "model": "personal-model"}}, format="json", **self.headers)
        self.assertEqual(updated.status_code, 200, updated.data)
        self.execution.refresh_from_db()
        self.assertEqual(self.execution.outputs.get(is_default=True).model_name, auto_model_name(self.execution))
        return Deployment.objects.get(pk=second.data["id"])

    def test_native_auto_model_selects_second_pool_and_preserves_pinned_models(self):
        second = self.configure()
        catalog = gateway.router_model_catalog(tenant=self.installation.tenant, router=self.execution)
        self.assertTrue(next(row for row in catalog if row["id"] == auto_model_name(self.execution))["available"])
        result = gateway.resolve_router_routing(request=self.request(), tenant=self.installation.tenant,
            router_id=str(self.execution.pk), payload={**self.payload(auto_model_name(self.execution)), "router_id": str(self.execution.pk)})
        self.assertEqual(result.deployments[0].pk, second.pk)
        self.assertEqual(result.trace["router"]["decision"]["status"], "selected")
        self.assertEqual(len(self.system_one_payloads), 1)
        self.assertEqual(set(self.system_one_payloads[0]), {"model", "state", "questions"})
        response = OpenAICompatibleClient().chat_completions(deployment=result.deployments[0], payload={"messages": [{"role": "user", "content": "fixture"}]})
        self.assertEqual(response.raw["choices"][0]["message"]["content"], "healthy")
        pinned = gateway.resolve_router_routing(request=self.request(), tenant=self.installation.tenant,
            router_id=str(self.execution.pk), payload={"model": self.output.model_name, "messages": []})
        self.assertEqual(pinned.deployments[0].pk, self.source_row.pk)
        self.assertEqual(len(self.system_one_payloads), 1)

    def test_invalid_decision_falls_back_and_disabled_provider_does_not_receive_calls(self):
        self.configure(); type(self).system_one_choice = "outside"
        result = gateway.resolve_router_routing(request=self.request(), tenant=self.installation.tenant,
            router_id=str(self.execution.pk), payload={"model": auto_model_name(self.execution), "messages": [{"role": "user", "content": "fixture"}]})
        self.assertEqual(result.deployments[0].pk, self.source_row.pk)
        self.assertEqual(result.trace["router"]["decision"]["status"], "fallback")
        self.assertEqual(len(self.system_one_payloads), 1)
        ProviderRuntimeAccount.objects.filter(source_provider_account_id=self.execution.decision_config["provider_connection_id"]).update(status="stopped")
        gateway.resolve_router_routing(request=self.request(), tenant=self.installation.tenant,
            router_id=str(self.execution.pk), payload={"model": auto_model_name(self.execution), "messages": []})
        self.assertEqual(len(self.system_one_payloads), 1)

    def test_invalid_config_is_atomic_and_does_not_create_a_router(self):
        before = Router.objects.count()
        response = self.post("/api/v1/routers/", {"name": "invalid", "strategy": "system_one",
            "decision_config": {"provider_connection_id": str(self.source_row.provider_account_id), "model": "personal-model"}})
        self.assertIn(response.status_code, [400, 403, 404])
        self.assertEqual(Router.objects.count(), before)

    def test_auto_output_works_through_aggregation_and_disappears_when_policy_is_disabled(self):
        second = self.configure()
        aggregate = services.create_router(request=self.request(), name="native aggregator", router_type="aggregation")
        services.add_router_child_binding(request=self.request(), router_id=str(aggregate.pk), data={
            "exposed_model_name": "smart", "child_output_id": self.execution.outputs.get(is_default=True).pk})
        services.deploy_router(request=self.request(), router_id=str(aggregate.pk))
        result = gateway.resolve_router_routing(request=self.request(), tenant=self.installation.tenant,
            router_id=str(aggregate.pk), payload={"model": "smart", "messages": [{"role": "user", "content": "fixture"}]})
        self.assertEqual(result.deployments[0].pk, second.pk)
        updated = services.update_router(request=self.request(), router_id=str(self.execution.pk), data={"strategy": "manual_priority"})
        self.assertFalse(updated.outputs.get(model_name=auto_model_name(updated)).enabled)

    def test_cross_organization_and_forged_endpoint_configuration_is_rejected(self):
        account_id = self.decision_provider()
        from apps.tenancy.models import Tenant
        from apps.providers.models import ProviderAccount
        foreign = Tenant.objects.create(name="Foreign decision org")
        ProviderAccount.objects.filter(pk=account_id).update(tenant=foreign)
        response = self.client.patch(f"/api/v1/routers/{self.execution.pk}/", {"strategy": "system_one",
            "decision_config": {"provider_connection_id": str(account_id), "model": "unlisted-native-model", "model_source": "explicit"}}, format="json", **self.headers)
        self.assertIn(response.status_code, [400, 403, 404])
        response = self.client.patch(f"/api/v1/routers/{self.execution.pk}/", {"decision_config": {
            "provider_connection_id": str(account_id), "model": "personal-model", "url": "http://127.0.0.1/private", "key": "must-not-store"}}, format="json", **self.headers)
        self.assertEqual(response.status_code, 400)

    def test_real_openai_api_key_calls_auto_without_extra_headers(self):
        self.configure()
        created = self.post(f"/api/v1/routers/{self.execution.pk}/api-access/", {"action": "ensure"})
        self.assertEqual(created.status_code, 200, created.data)
        from rest_framework.test import APIClient
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer " + created.data["credentials"]["gateway_api_key"])
        catalog = client.get("/api/v1/openai/v1/models")
        self.assertEqual(catalog.status_code, 200, catalog.data)
        self.assertIn(auto_model_name(self.execution), [row["id"] for row in catalog.data["data"]])
        response = client.post("/api/v1/openai/v1/chat/completions", {"model": auto_model_name(self.execution),
            "messages": [{"role": "user", "content": "fixture"}]}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["choices"][0]["message"]["content"], "healthy")
        self.assertEqual(len(self.system_one_payloads), 1)

    def test_unlisted_model_requires_explicit_opt_in(self):
        self.configure()
        config = {**self.execution.decision_config, "model": "unlisted-native-model"}
        response = self.client.patch(f"/api/v1/routers/{self.execution.pk}/", {"decision_config": config}, format="json", **self.headers)
        self.assertEqual(response.status_code, 400)
        response = self.client.patch(f"/api/v1/routers/{self.execution.pk}/", {"decision_config": {**config, "model_source": "unknown"}}, format="json", **self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.system_one_payloads), 0, "configuration must not issue paid probes")

    def test_explicit_unlisted_model_can_route_but_cannot_resurrect_a_disabled_offer(self):
        second = self.configure()
        config = {**self.execution.decision_config, "model": "unlisted-native-model", "model_source": "explicit"}
        response = self.client.patch(f"/api/v1/routers/{self.execution.pk}/", {"decision_config": config}, format="json", **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        self.execution.refresh_from_db()
        self.assertEqual(self.execution.decision_config["model_source"], "explicit")
        runtime = ProviderRuntimeAccount.objects.get(source_provider_account_id=config["provider_connection_id"])
        self.assertFalse(runtime.model_offers.filter(upstream_model_id=config["model"]).exists(), "explicit IDs must not fabricate advertised Offers")
        result = gateway.resolve_router_routing(request=self.request(), tenant=self.installation.tenant, router_id=str(self.execution.pk),
            payload={"model": auto_model_name(self.execution), "messages": [{"role": "user", "content": "fixture"}]})
        self.assertEqual(result.deployments[0].pk, second.pk)
        self.assertEqual(self.system_one_payloads[0]["model"], "unlisted-native-model")
        offer = runtime.model_offers.get(upstream_model_id="personal-model")
        offer.status = "disabled"; offer.save(update_fields=["status"])
        response = self.client.patch(f"/api/v1/routers/{self.execution.pk}/", {"decision_config": {**config, "model": "personal-model"}}, format="json", **self.headers)
        self.assertEqual(response.status_code, 400, "explicit must not bypass an existing disabled Offer")
        self.execution.refresh_from_db()
        self.assertEqual(self.execution.decision_config["model"], "unlisted-native-model")
