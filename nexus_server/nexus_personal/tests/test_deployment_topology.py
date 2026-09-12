"""Real local Source/Pool/Execution/Aggregation graph without commercial tables."""
import json
from uuid import uuid4
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.deployments.models import Deployment, ModelGroup, ModelGroupDeployment
from apps.providers.models import ProviderRuntimeAccount, ProviderAccount
from apps.gateway.models import GatewayRequestLog
from .provider_http_fixture import ProviderHTTPFixture
from . import test_deployment_http as source_http
from . import test_router_http as router_http


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1")
class PersonalDeploymentTopologyTests(ProviderHTTPFixture, TestCase):
    source = source_http.PersonalDeploymentHTTPTests.source
    post = source_http.PersonalDeploymentHTTPTests.post
    create_router = router_http.PersonalRouterHTTPTests.create_router

    def setUp(self):
        super().setUp()
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.installation.owner)
        self.client.get("/api/v1/public/bootstrap/")
        self.headers = {"HTTP_X_CSRFTOKEN": self.client.cookies["csrftoken"].value, "HTTP_ORIGIN": "http://testserver"}

    def graph(self):
        response = self.client.get("/api/v1/topology/")
        self.assertEqual(response.status_code, 200, response.data)
        text = json.dumps(response.data, default=str)
        for secret in ("local-provider-test-key", "encrypted_key", "Authorization", "127.0.0.1",
                       "marketplace", "pool_contribution", "total_cost"):
            self.assertNotIn(secret, text)
        return response.data

    def create_source(self):
        self.source_row = Deployment.objects.get(pk=self.source()["id"])
        self.group = ModelGroup.objects.get()
        return self.source_row

    def test_empty_graph_is_real_empty_catalog(self):
        payload = self.graph()
        self.assertEqual([payload[name] for name in ("runtimes", "sources", "pools", "routers")], [[], [], [], []])
        self.assertEqual(payload["summary"]["runtime_count"], 0)

    def test_discovered_source_pool_routers_and_actual_usage_survive_refresh(self):
        source = self.create_source()
        execution, path = self.create_router()
        self.assertEqual(self.post(path + "deploy/").status_code, 201)
        aggregation, aggregate_path = self.create_router(name="personal-api", aggregate=True)
        output = self.client.get(path + "outputs/").data[0]
        bound = self.post(aggregate_path + "child-bindings/", {
            "child_output_id": output["id"], "exposed_model_name": "personal-chat"})
        self.assertEqual(bound.status_code, 201, bound.data)
        GatewayRequestLog.objects.create(tenant=self.installation.tenant, actor=self.installation.owner,
            project_id=str(self.installation.project_id), router_id=execution["id"], deployment=source,
            consumer_source=source, selected_model_group=self.group, model="personal-model", status="success", latency_ms=12)
        payload = self.graph()
        self.assertEqual(payload["summary"]["runtime_count"], 1)
        self.assertEqual(payload["summary"]["source_count"], 1)
        self.assertEqual(payload["runtimes"][0]["model_offers"][0]["source_ids"], [str(source.pk)])
        self.assertEqual(payload["sources"][0]["runtime_id"], str(source.provider_runtime_id))
        self.assertEqual(payload["sources"][0]["source_type"], "provider_runtime")
        self.assertEqual(payload["pools"][0]["sources"][0]["source_id"], str(source.pk))
        routers = {row["id"]: row for row in payload["routers"]}
        self.assertEqual(routers[execution["id"]]["request_count"], 1)
        self.assertEqual(routers[execution["id"]]["average_latency_ms"], 12)
        model = routers[aggregation["id"]]["api_models"][0]
        self.assertEqual(model["execution_router_id"], execution["id"])
        self.assertEqual(model["model_name"], "personal-chat")
        self.assertEqual(self.graph(), payload)

    def test_live_health_and_deleted_source_edges_are_not_fabricated(self):
        source = self.create_source()
        ProviderRuntimeAccount.objects.filter(pk=source.provider_runtime_id).update(status="stopped")
        payload = self.graph()
        self.assertEqual(payload["sources"][0]["health_status"], "unhealthy")
        self.assertEqual(payload["summary"]["health"]["unhealthy"], 1)
        self.assertEqual(payload["pools"][0]["health_status"], "unhealthy")
        Deployment.objects.filter(pk=source.pk).update(status="deleted")
        payload = self.graph()
        self.assertEqual(payload["sources"], [])
        self.assertEqual(payload["pools"][0]["sources"], [])
        self.assertEqual(payload["runtimes"][0]["model_offers"][0]["source_ids"], [])

    def test_foreign_runtime_and_changed_credential_do_not_leak_into_graph(self):
        source = self.create_source()
        for field in ("provider_account", "source_provider_account"):
            runtime = source.provider_runtime
            account = getattr(runtime, field)
            self.assertIsNotNone(account)
            original = account.created_by_id
            account.created_by = self.other
            account.save(update_fields=["created_by"])
            payload = self.graph()
            self.assertEqual(payload["runtimes"], [])
            self.assertEqual(payload["sources"], [])
            self.assertEqual(payload["pools"][0]["sources"], [])
            account.created_by_id = original
            account.save(update_fields=["created_by"])
        ProviderRuntimeAccount.objects.filter(pk=source.provider_runtime_id).update(owner=self.other)
        self.assertEqual(self.graph()["runtimes"], [])
        self.assertEqual(self.graph()["sources"], [])

    def test_authentication_and_context_cannot_be_overridden(self):
        self.create_source()
        anonymous = APIClient()
        self.assertIn(anonymous.get("/api/v1/topology/").status_code, (401, 403))
        anonymous.force_login(self.other)
        self.assertIn(anonymous.get("/api/v1/topology/").status_code, (401, 403))
        for header in ("HTTP_X_NEXUS_PROJECT", "HTTP_X_NEXUS_TENANT"):
            self.assertEqual(self.client.get("/api/v1/topology/", **{header: str(uuid4())}).status_code, 403)

    def test_stale_pool_edges_and_alternate_credentials_remain_owner_scoped(self):
        source = self.create_source()
        hidden = ModelGroup.objects.create(tenant=self.installation.tenant, project=self.installation.project,
            canonical_model=source.canonical_model, name="foreign-pool-marker", created_by=self.other)
        ModelGroupDeployment.objects.create(model_group=hidden, deployment=source)
        payload = self.graph()
        self.assertNotIn(str(hidden.pk), json.dumps(payload, default=str))
        account = ProviderAccount.objects.create(tenant=self.installation.tenant, provider=source.provider,
            account_id="foreign-credential", created_by=self.other)
        ProviderRuntimeAccount.objects.filter(pk=source.provider_runtime_id).update(source_provider_account=account)
        payload = self.graph()
        self.assertEqual(payload["runtimes"], [])
        self.assertEqual(payload["sources"], [])
        self.assertEqual(payload["pools"][0]["sources"], [])
