"""Real shared routing + HTTP upstream, before full Gateway dispatch composition."""
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings
from rest_framework import exceptions
from rest_framework.test import APIClient
from apps.deployments.models import Deployment, ModelGroup
from apps.gateway import services as gateway
from apps.gateway.integration import gateway_integration
from apps.gateway.provider_clients import OpenAICompatibleClient, ProviderClientError
from apps.gateway.models import GatewayRequestLog
from apps.routers import services
from apps.routers.models import Router
from .provider_http_fixture import ProviderHTTPFixture
from . import test_deployment_http as source_http


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1")
class PersonalGatewayRoutingTests(ProviderHTTPFixture, TestCase):
    source = source_http.PersonalDeploymentHTTPTests.source
    post = source_http.PersonalDeploymentHTTPTests.post
    runtime = source_http.PersonalDeploymentHTTPTests.runtime

    def setUp(self):
        super().setUp()
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.installation.owner)
        self.client.get("/api/v1/public/bootstrap/")
        self.headers = {"HTTP_X_CSRFTOKEN": self.client.cookies["csrftoken"].value, "HTTP_ORIGIN": "http://testserver"}
        self.source_row = Deployment.objects.get(pk=self.source()["id"])
        self.group = ModelGroup.objects.get()
        self.execution = services.create_router(request=self.request(), name="execution", model_group_ids=[self.group.pk])
        services.deploy_router(request=self.request(), router_id=str(self.execution.pk))
        self.output = self.execution.outputs.get()
        self.aggregate = services.create_router(request=self.request(), name="aggregate", router_type="aggregation")
        services.add_router_child_binding(request=self.request(), router_id=str(self.aggregate.pk),
            data={"child_output_id": self.output.pk, "exposed_model_name": "my-export"})
        services.deploy_router(request=self.request(), router_id=str(self.aggregate.pk))
        type(self).calls.clear()

    def payload(self, model="my-export", router=True):
        data = {"model": model, "messages": [{"role": "user", "content": "routing-check"}]}
        if router:
            data["router_id"] = str(self.aggregate.pk)
        return data

    def resolve(self, payload=None, request=None):
        return gateway.resolve_deployment_candidates(request=request or self.request(), tenant=self.installation.tenant,
            payload=payload or self.payload())

    def test_shared_execution_and_aggregation_select_a_real_callable_upstream(self):
        for payload in (self.payload(), {**self.payload(self.output.model_name), "router_id": str(self.execution.pk)},
                        self.payload(self.group.name, router=False), self.payload(self.source_row.deployment_id, router=False)):
            candidates = self.resolve(payload)
            self.assertEqual([candidate.pk for candidate in candidates], [self.source_row.pk])
            response = OpenAICompatibleClient().chat_completions(deployment=candidates[0], payload=gateway.provider_request_payload(payload))
            self.assertEqual(response.raw["choices"][0]["message"]["content"], "healthy")
            self.assertEqual(self.calls[-1], ("POST", "/v1/chat/completions", "personal-model"))
        resolution = gateway.resolve_router_routing(request=self.request(), tenant=self.installation.tenant,
            router_id=str(self.aggregate.pk), payload=self.payload())
        self.assertEqual([stage["type"] for stage in resolution.trace["path"]], ["aggregation", "execution"])
        self.assertEqual(resolution.deployments[0]._nexus_consumer_source_id, str(self.source_row.pk))
        self.assertEqual(self.calls.__len__(), 4)

    def test_real_upstream_failure_and_source_stop_recovery_never_invent_success(self):
        candidate = self.resolve()[0]
        type(self).probe_status = 503
        with self.assertRaises(ProviderClientError):
            OpenAICompatibleClient().chat_completions(deployment=candidate, payload=self.payload())
        type(self).probe_status = 200
        Deployment.objects.filter(pk=self.source_row.pk).update(status="disabled")
        with self.assertRaises(exceptions.APIException):
            self.resolve()
        Deployment.objects.filter(pk=self.source_row.pk).update(status="active")
        candidate = self.resolve()[0]
        self.assertEqual(candidate.pk, self.source_row.pk)
        self.assertEqual(OpenAICompatibleClient().chat_completions(deployment=candidate, payload=self.payload()).raw[
            "choices"][0]["message"]["content"], "healthy")

    def test_current_owner_pool_router_and_credential_scope_are_rechecked(self):
        for model, obj, field in ((Router, self.aggregate, "created_by"), (Router, self.execution, "created_by"),
                                  (ModelGroup, self.group, "created_by"), (Deployment, self.source_row, "created_by")):
            model.objects.filter(pk=obj.pk).update(**{field: self.other})
            with self.assertRaises(exceptions.APIException):
                self.resolve()
            model.objects.filter(pk=obj.pk).update(**{field: self.installation.owner})
        account = self.source_row.provider_account
        type(account).objects.filter(pk=account.pk).update(created_by=self.other)
        with self.assertRaises(exceptions.APIException):
            self.resolve()
        self.assertEqual(self.calls, [])

    def test_foreign_principals_headers_models_and_forged_keys_are_rejected(self):
        request = self.request(self.other)
        with self.assertRaises(exceptions.APIException):
            self.resolve(request=request)
        request = self.request()
        request.headers = {"X-Nexus-Project": str(uuid4())}
        request.META["HTTP_X_NEXUS_PROJECT"] = request.headers["X-Nexus-Project"]
        with self.assertRaises(exceptions.APIException):
            self.resolve(request=request)
        for payload in (self.payload("not-exported"), self.payload("not-present", router=False)):
            with self.assertRaises(exceptions.APIException):
                self.resolve(payload)
        with self.assertRaises(exceptions.PermissionDenied):
            gateway.enforce_api_key_policy(api_key=SimpleNamespace(scope="gateway"), model="personal-model")
        self.assertEqual(self.calls, [])

    def test_disabled_links_runtime_and_offer_remove_available_targets(self):
        link = self.group.deployment_links.get()
        for obj, field, invalid in ((link, "enabled", False), (self.source_row.provider_runtime, "status", "disabled"),
                                    (self.source_row.runtime_model_offer, "health_status", "unhealthy")):
            original = getattr(obj, field)
            type(obj).objects.filter(pk=obj.pk).update(**{field: invalid})
            with self.assertRaises(exceptions.APIException):
                self.resolve()
            type(obj).objects.filter(pk=obj.pk).update(**{field: original})
        self.assertEqual(self.resolve()[0].pk, self.source_row.pk)

    def test_unadmitted_dispatch_is_rejected_and_failure_count_is_scoped(self):
        request = self.request()
        request.request_id = "same-request-id"
        request.project_id = str(self.installation.project.pk)
        for actor, project, code in ((self.installation.owner, request.project_id, "PROVIDER_FAILED"),
                                     (self.other, request.project_id, "PROVIDER_FAILED"),
                                     (self.installation.owner, str(uuid4()), "PROVIDER_FAILED"),
                                     (self.installation.owner, request.project_id, "PROVIDER_ACCOUNT_NOT_FOUND")):
            GatewayRequestLog.objects.create(tenant=self.installation.tenant, actor=actor, project_id=project,
                request_id=request.request_id, model="m", status="failed", error_code=code)
        self.assertEqual(gateway.count_previous_provider_failures(request=request), 1)
        with self.assertRaises(exceptions.APIException):
            gateway.reserve_provider_capacity_for_deployment(request=request, deployment=self.source_row, provider_payload=self.payload())
        with self.assertRaises(exceptions.NotFound):
            gateway.pool_deployments_for_model(tenant=self.installation.tenant, model_name="m")
        for name in ("APIKey", "ProviderPoolContribution", "ProviderCapacityReservation"):
            with self.assertRaises(AttributeError):
                getattr(gateway, name)

    def test_five_source_strategies_and_failed_source_fallback_use_actual_targets(self):
        runtime, offer = self.runtime("second-upstream")
        response = self.post("/api/v1/deployments/", {"source_type": "provider_runtime",
            "provider_runtime_id": str(runtime.pk), "model_offer_id": str(offer.pk),
            "deployment_id": "second-source", "model_group_id": str(self.group.pk)})
        self.assertEqual(response.status_code, 201, response.data)
        second = Deployment.objects.get(pk=response.data["id"])
        for source, price, latency, health, order in ((self.source_row, "0.1", 40, "degraded", 1),
                                                     (second, "0.01", 10, "healthy", 2)):
            Deployment.objects.filter(pk=source.pk).update(pricing_rate=Decimal(price), last_latency_ms=latency, health_status=health)
            self.group.deployment_links.filter(deployment=source).update(priority=order, fallback_order=order, weight=50)
        for strategy in ("fallback", "lowest_cost", "lowest_latency", "best_health", "weighted"):
            ModelGroup.objects.filter(pk=self.group.pk).update(routing_strategy=strategy)
            selected = self.resolve()
            self.assertEqual({item.pk for item in selected}, {self.source_row.pk, second.pk})
            if strategy in {"lowest_cost", "lowest_latency", "best_health"}:
                self.assertEqual(selected[0].pk, second.pk)
        ModelGroup.objects.filter(pk=self.group.pk).update(routing_strategy="fallback")
        for changes in ({"health_status": "unhealthy"}, {"status": "disabled"}, {"status": "deleted"}):
            Deployment.objects.filter(pk=self.source_row.pk).update(**changes)
            self.assertEqual([item.pk for item in self.resolve()], [second.pk])
        candidate = self.resolve()[0]
        reply = OpenAICompatibleClient().chat_completions(deployment=candidate, payload=self.payload())
        self.assertEqual(reply.raw["choices"][0]["message"]["content"], "healthy")
