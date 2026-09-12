from __future__ import annotations

from decimal import Decimal
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models import CanonicalModel, Deployment, ModelGroup, ModelGroupDeployment
from apps.gateway.models import GatewayRequestLog
from apps.gateway.services import sanitize_custom_router_reason
from apps.providers.models import Provider, ProviderAccount
from apps.routers.models import Router, RouterChildBinding, RouterModelGroupBinding, RouterOutput
from apps.tenancy.models import Membership, Project, Tenant


class TopologyRoutingTraceTests(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(username="fabric-owner", password="password")
        self.tenant = Tenant.objects.create(name="Fabric Tenant", slug="fabric-tenant")
        Membership.objects.create(tenant=self.tenant, user=self.owner, role=Membership.ROLE_OWNER)
        self.provider = Provider.objects.create(name="fabric-provider", display_name="Fabric Provider")
        self.account = ProviderAccount.objects.create(
            tenant=self.tenant,
            provider=self.provider,
            account_id="fabric-main",
            url="https://private.example.test/v1",
            encrypted_key="encrypted-secret-must-not-leak",
            status="active",
            created_by=self.owner,
        )
        self.fast_source = self._source("fast-source", "model-fast", "0.020000", 80)
        self.cheap_source = self._source("cheap-source", "model-cheap", "0.001000", 240)
        self.fast_pool = self._pool("fast-pool", self.fast_source)
        self.cheap_pool = self._pool("cheap-pool", self.cheap_source)
        self.router = Router.objects.create(
            tenant=self.tenant,
            name="Cost fabric",
            strategy=Router.STRATEGY_LOWEST_COST_POOL,
            status=Router.STATUS_DEPLOYED,
            created_by=self.owner,
        )
        RouterModelGroupBinding.objects.create(
            router=self.router,
            model_group=self.fast_pool,
            provider_name="*",
            model_group_name=self.fast_pool.name,
            priority=1,
        )
        RouterModelGroupBinding.objects.create(
            router=self.router,
            model_group=self.cheap_pool,
            provider_name="*",
            model_group_name=self.cheap_pool.name,
            priority=2,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.headers = {"HTTP_X_NEXUS_TENANT": str(self.tenant.id)}

    def test_topology_returns_safe_runtime_source_pool_router_graph(self) -> None:
        response = self.client.get("/api/v1/topology/", **self.headers)

        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["summary"]["source_count"], 2)
        self.assertEqual(data["summary"]["pool_count"], 2)
        self.assertEqual(data["summary"]["router_count"], 1)
        self.assertEqual(data["routers"][0]["pool_ids"], [str(self.fast_pool.id), str(self.cheap_pool.id)])
        self.assertEqual(data["routers"][0]["resource_ownership"]["label"], "Organization shared")
        self.assertEqual(data["pools"][0]["resource_ownership"]["scope"], "organization")
        self.assertEqual(data["sources"][0]["resource_ownership"]["scope"], "organization")
        serialized = str(data)
        self.assertNotIn("private.example.test", serialized)
        self.assertNotIn("encrypted-secret-must-not-leak", serialized)

    def test_topology_includes_aggregation_api_model_and_execution_router_lineage(self) -> None:
        output = RouterOutput.objects.create(
            router=self.router,
            model_group=self.cheap_pool,
            model_name="cheap-model",
            is_default=True,
        )
        aggregation = Router.objects.create(
            tenant=self.tenant,
            name="Unified API",
            router_type=Router.TYPE_AGGREGATION,
            status=Router.STATUS_DEPLOYED,
            created_by=self.owner,
        )
        binding = RouterChildBinding.objects.create(
            router=aggregation,
            exposed_model_name="chat",
            child_output=output,
        )

        response = self.client.get("/api/v1/topology/", **self.headers)

        self.assertEqual(response.status_code, 200, response.content)
        routers = {item["name"]: item for item in response.json()["data"]["routers"]}
        self.assertEqual(routers["Cost fabric"]["router_type"], Router.TYPE_EXECUTION)
        aggregate = routers["Unified API"]
        self.assertEqual(aggregate["router_type"], Router.TYPE_AGGREGATION)
        self.assertEqual(aggregate["pool_ids"], [])
        self.assertEqual(aggregate["execution_router_count"], 1)
        self.assertEqual(
            aggregate["api_models"],
            [
                {
                    "binding_id": str(binding.id),
                    "model_name": "chat",
                    "available": True,
                    "code": "",
                    "message": "",
                    "execution_router_id": str(self.router.id),
                    "execution_router_name": "Cost fabric",
                    "execution_router_status": Router.STATUS_DEPLOYED,
                    "execution_model_name": "cheap-model",
                }
            ],
        )

    def test_router_dry_run_explains_pool_stage_then_source_stage(self) -> None:
        response = self.client.post(
            f"/api/v1/routers/{self.router.id}/test/",
            {"model": "probe", "messages": [{"role": "user", "content": "secret prompt"}]},
            format="json",
            **self.headers,
        )

        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        trace = data["trace"]
        self.assertEqual(trace["router"]["selected_pool_id"], str(self.cheap_pool.id))
        self.assertEqual(trace["pool"]["selected_source_id"], str(self.cheap_source.id))
        self.assertTrue(all("source_id" not in candidate for candidate in trace["router"]["candidates"]))
        self.assertTrue(all("pool_id" not in candidate for candidate in trace["pool"]["candidates"]))
        self.assertNotIn("secret prompt", str(trace))

    def test_trace_endpoint_returns_persisted_path_and_old_log_compatibility(self) -> None:
        trace = {
            "version": 1,
            "router": {"strategy": self.router.strategy, "candidates": [], "selected_pool_id": str(self.cheap_pool.id), "reason": "Selected by Router pool policy."},
            "pool": {"pool_id": str(self.cheap_pool.id), "strategy": self.cheap_pool.routing_strategy, "candidates": [], "selected_source_id": str(self.cheap_source.id)},
        }
        GatewayRequestLog.objects.create(
            tenant=self.tenant,
            router=self.router,
            router_strategy=self.router.strategy,
            model="probe",
            deployment=self.cheap_source,
            consumer_source=self.cheap_source,
            selected_model_group=self.cheap_pool,
            routing_trace=trace,
            provider=self.provider.name,
            status=GatewayRequestLog.STATUS_SUCCESS,
            latency_ms=123,
            request_id="req-new",
        )
        GatewayRequestLog.objects.create(
            tenant=self.tenant,
            router=self.router,
            model="legacy",
            provider=self.provider.name,
            status=GatewayRequestLog.STATUS_SUCCESS,
            request_id="req-old",
        )

        response = self.client.get(f"/api/v1/routers/{self.router.id}/traces/?limit=10", **self.headers)

        self.assertEqual(response.status_code, 200, response.content)
        items = response.json()["data"]["items"]
        by_request = {item["request_id"]: item for item in items}
        self.assertIsNone(by_request["req-old"]["trace"])
        self.assertEqual(by_request["req-new"]["selected_pool_id"], str(self.cheap_pool.id))
        self.assertEqual(by_request["req-new"]["selected_source"], self.cheap_source.deployment_id)

    def test_empty_pool_binding_payload_removes_all_pool_bindings(self) -> None:
        response = self.client.post(
            f"/api/v1/routers/{self.router.id}/model-groups/",
            {"model_group_ids": []},
            format="json",
            **self.headers,
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"], [])
        self.assertFalse(self.router.model_group_bindings.filter(status="active").exists())

    def test_custom_reason_redacts_prompt_content(self) -> None:
        result = sanitize_custom_router_reason(
            "Choose fast because private customer question is short",
            payload={"messages": [{"role": "user", "content": "private customer question"}]},
        )
        self.assertEqual(result, "Choose fast because [REDACTED] is short")

    def test_topology_respects_project_scope(self) -> None:
        project_a = Project.objects.create(tenant=self.tenant, name="Project A")
        project_b = Project.objects.create(tenant=self.tenant, name="Project B")
        source_a = self._source("project-a-source", "project-model", "0.010000", 90)
        source_a.project = project_a
        source_a.save(update_fields=["project", "updated_at"])
        source_b = self._source("project-b-source", "project-model", "0.020000", 100)
        source_b.project = project_b
        source_b.save(update_fields=["project", "updated_at"])
        pool_a = self._pool("project-a-pool", source_a)
        pool_a.project = project_a
        pool_a.save(update_fields=["project", "updated_at"])
        pool_b = self._pool("project-b-pool", source_b)
        pool_b.project = project_b
        pool_b.save(update_fields=["project", "updated_at"])
        router_a = Router.objects.create(tenant=self.tenant, name="Project A Router", status=Router.STATUS_DEPLOYED)
        RouterModelGroupBinding.objects.create(router=router_a, model_group=pool_a, provider_name="*", model_group_name=pool_a.name)
        output_a = RouterOutput.objects.create(router=router_a, model_group=pool_a, model_name="project-a-model", is_default=True)
        router_b = Router.objects.create(tenant=self.tenant, name="Project B Router", status=Router.STATUS_DEPLOYED)
        RouterModelGroupBinding.objects.create(router=router_b, model_group=pool_b, provider_name="*", model_group_name=pool_b.name)
        output_b = RouterOutput.objects.create(router=router_b, model_group=pool_b, model_name="project-b-model", is_default=True)
        aggregation = Router.objects.create(
            tenant=self.tenant,
            name="Project Aggregation",
            router_type=Router.TYPE_AGGREGATION,
            status=Router.STATUS_DEPLOYED,
        )
        RouterChildBinding.objects.create(router=aggregation, exposed_model_name="model-a", child_output=output_a)
        RouterChildBinding.objects.create(router=aggregation, exposed_model_name="model-b", child_output=output_b)

        response = self.client.get(
            "/api/v1/topology/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
            HTTP_X_NEXUS_PROJECT=str(project_a.id),
        )

        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(
            [item["name"] for item in data["sources"]],
            ["cheap-source", "fast-source", "project-a-source"],
        )
        self.assertEqual(
            [item["name"] for item in data["pools"]],
            ["cheap-pool", "fast-pool", "project-a-pool"],
        )
        self.assertEqual(
            {item["name"] for item in data["routers"]},
            {"Cost fabric", "Project Aggregation", "Project A Router"},
        )
        self.assertNotIn("project-b-source", {item["name"] for item in data["sources"]})
        self.assertNotIn("project-b-pool", {item["name"] for item in data["pools"]})
        self.assertNotIn("Project B Router", {item["name"] for item in data["routers"]})
        aggregate = next(item for item in data["routers"] if item["name"] == "Project Aggregation")
        self.assertEqual([item["model_name"] for item in aggregate["api_models"]], ["model-a"])
        self.assertEqual(aggregate["api_models"][0]["execution_router_id"], str(router_a.id))

    def test_trace_cursor_pages_without_cross_tenant_leakage(self) -> None:
        for index in range(3):
            GatewayRequestLog.objects.create(
                tenant=self.tenant,
                router=self.router,
                model="probe",
                provider=self.provider.name,
                status=GatewayRequestLog.STATUS_SUCCESS,
                request_id=f"req-{index}",
            )
        other_tenant = Tenant.objects.create(name="Other", slug="other-fabric-tenant")
        other_router = Router.objects.create(tenant=other_tenant, name="Other Router", status=Router.STATUS_DEPLOYED)
        GatewayRequestLog.objects.create(
            tenant=other_tenant,
            router=other_router,
            model="probe",
            provider="other",
            status=GatewayRequestLog.STATUS_SUCCESS,
            request_id="must-not-leak",
        )

        first = self.client.get(f"/api/v1/routers/{self.router.id}/traces/?limit=1", **self.headers)
        self.assertEqual(first.status_code, 200, first.content)
        first_data = first.json()["data"]
        self.assertEqual(len(first_data["items"]), 1)
        self.assertIsNotNone(first_data["next_cursor"])
        second = self.client.get(
            f"/api/v1/routers/{self.router.id}/traces/?limit=10&cursor={quote(first_data['next_cursor'])}",
            **self.headers,
        )
        self.assertEqual(second.status_code, 200, second.content)
        request_ids = {item["request_id"] for item in first_data["items"] + second.json()["data"]["items"]}
        self.assertEqual(request_ids, {"req-0", "req-1", "req-2"})
        self.assertNotIn("must-not-leak", request_ids)

    def _source(self, deployment_id: str, model: str, price: str, latency: int) -> Deployment:
        canonical, _ = CanonicalModel.objects.get_or_create(
            key=model,
            defaults={"display_name": model},
        )
        return Deployment.objects.create(
            tenant=self.tenant,
            provider=self.provider,
            provider_account=self.account,
            deployment_id=deployment_id,
            canonical_model=canonical,
            upstream_model_id=model,
            endpoint=self.account.url,
            pricing_rate=Decimal(price),
            health_status=Deployment.HEALTH_HEALTHY,
            last_latency_ms=latency,
            created_by=self.owner,
        )

    def _pool(self, name: str, source: Deployment) -> ModelGroup:
        pool = ModelGroup.objects.create(
            tenant=self.tenant,
            canonical_model=source.canonical_model,
            name=name,
            display_name=name.title(),
            created_by=self.owner,
        )
        ModelGroupDeployment.objects.create(model_group=pool, deployment=source)
        return pool
