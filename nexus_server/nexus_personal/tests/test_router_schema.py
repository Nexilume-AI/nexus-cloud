"""Real Router schema and Pool preview, not a Router execution E2E claim."""
from decimal import Decimal
from uuid import uuid4
from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.db import connection, IntegrityError, transaction
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.deployments.models import Deployment, ModelGroup
from apps.routers import models
from apps.tenancy.models import Project, Tenant
from .provider_http_fixture import ProviderHTTPFixture
from . import test_deployment_http as source_http


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1")
class PersonalRouterSchemaTests(ProviderHTTPFixture, TestCase):
    # Reuse the existing real Provider -> Source HTTP fixture, not a new driver.
    source = source_http.PersonalDeploymentHTTPTests.source
    post = source_http.PersonalDeploymentHTTPTests.post
    runtime = source_http.PersonalDeploymentHTTPTests.runtime

    def setUp(self):
        super().setUp()
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.installation.owner)
        self.assertEqual(self.client.get("/api/v1/public/bootstrap/").status_code, 200)
        self.headers = {"HTTP_X_CSRFTOKEN": self.client.cookies["csrftoken"].value, "HTTP_ORIGIN": "http://testserver"}

    def router(self, name="execution", **overrides):
        return models.Router.objects.create(**{
            "tenant": self.installation.tenant, "project": self.installation.project,
            "created_by": self.installation.owner, "name": name, **overrides})

    def pool(self):
        source = Deployment.objects.get(pk=self.source()["id"])
        group = ModelGroup.objects.get()
        self.path = f"/api/v1/models/{group.pk}/routing/preview/"
        return source, group

    def preview(self, group, strategy="fallback", **data):
        return self.post(self.path, {"expected_revision": group.routing_revision,
            "routing_strategy": strategy, **data})

    def test_real_schema_excludes_private_tables_and_fields(self):
        registered = list(apps.get_app_config("routers").get_models())
        self.assertEqual(len(registered), 8)
        tables = set(connection.introspection.table_names())
        for model in registered:
            self.assertIn(model._meta.db_table, tables)
            model.objects.count()
        self.assertNotIn("routers_routerpricing", tables)
        with self.assertRaises(AttributeError):
            getattr(models, "RouterPricing")
        for model, name in ((models.RouterRuntimeInvocation, "api_key"),
                            (models.RouterProviderPreference, "pool_contribution")):
            with self.assertRaises(FieldDoesNotExist):
                model._meta.get_field(name)
            with connection.cursor() as cursor:
                columns = connection.introspection.get_table_description(cursor, model._meta.db_table)
            self.assertNotIn(name + "_id", {column.name for column in columns})

    def test_execution_aggregation_version_and_invocation_graph_is_real(self):
        source, group = self.pool()
        router = self.router()
        version = models.RouterVersion.objects.create(router=router, version="1", router_file_path="router.py")
        models.RouterDeployment.objects.create(router=router, version=version, status="active")
        output = models.RouterOutput.objects.create(router=router, model_group=group, model_name="personal-model")
        aggregate = self.router("aggregate", router_type="aggregation")
        binding = models.RouterChildBinding.objects.create(router=aggregate, child_output=output, exposed_model_name="my-model")
        preference = models.RouterProviderPreference.objects.create(tenant=router.tenant, router=router, provider_account=source.provider_account)
        invocation = models.RouterRuntimeInvocation.objects.create(tenant=router.tenant, router=router, version=version,
            selected_deployment=source, actor=self.installation.owner, status="success", request_id="schema-only")
        binding.refresh_from_db()
        invocation.refresh_from_db()
        self.assertEqual(binding.child_output.model_group_id, group.pk)
        self.assertEqual(invocation.selected_deployment.provider_account_id, preference.provider_account_id)
        with self.assertRaises(IntegrityError), transaction.atomic():
            models.RouterVersion.objects.create(router=router, version="1", router_file_path="other.py")
        with self.assertRaises(IntegrityError), transaction.atomic():
            models.RouterChildBinding.objects.create(router=aggregate, child_output=output, exposed_model_name="my-model")
        with self.assertRaises(IntegrityError), transaction.atomic():
            models.RouterOutput.objects.create(router=router, model_group=group, model_name="personal-model")

    def test_five_preview_strategies_use_actual_sources_and_router_impact(self):
        source, group = self.pool()
        runtime, offer = self.runtime("second-upstream")
        created = self.post("/api/v1/deployments/", {"source_type": "provider_runtime", "provider_runtime_id": str(runtime.pk),
            "model_offer_id": str(offer.pk), "deployment_id": "second-source", "model_group_id": str(group.pk)})
        self.assertEqual(created.status_code, 201, created.data)
        group.refresh_from_db()
        second = Deployment.objects.get(pk=created.data["id"])
        for deployment, price, latency, health in ((source, "0.1", 40, "healthy"), (second, "0.2", 10, "degraded")):
            deployment.pricing_rate, deployment.last_latency_ms, deployment.health_status = Decimal(price), latency, health
            deployment.save(update_fields=["pricing_rate", "last_latency_ms", "health_status"])
        for deployment, weight, order in ((source, 20, 1), (second, 80, 2)):
            group.deployment_links.filter(deployment=deployment).update(weight=weight, fallback_order=order)
        execution = self.router()
        models.RouterModelGroupBinding.objects.create(router=execution, model_group=group, provider_name="upstream", model_group_name=group.name)
        models.RouterOutput.objects.create(router=execution, model_group=group, model_name="personal-model")
        other_router = self.router("other-execution")
        models.RouterOutput.objects.create(router=other_router, model_group=group, model_name="personal-model")
        for strategy, first in (("fallback", source), ("lowest_cost", source), ("lowest_latency", second),
                                ("best_health", source), ("weighted", second)):
            response = self.preview(group, strategy)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data["affected_router_count"], 2)
            self.assertEqual(response.data["candidate_count"], 2)
            self.assertEqual(response.data["healthy_candidate_count"], 1)
            self.assertEqual(response.data["candidates"][0]["source_id"], str(first.pk))
            if strategy == "weighted":
                self.assertEqual(response.data["candidates"][0]["traffic_share_percent"], 80)
        old_revision = group.routing_revision
        group.refresh_from_db()
        self.assertEqual(group.routing_revision, old_revision)

    def test_unbound_pool_and_hidden_or_deleted_routers_never_invent_impact(self):
        source, group = self.pool()
        other_project = Project.objects.create(tenant=self.installation.tenant, name="outside")
        foreign_tenant = Tenant.objects.create(name="outside")
        for changes in ({"created_by": self.other}, {"project": other_project}, {"tenant": foreign_tenant}, {"status": "deleted"}):
            router = self.router(**changes)
            models.RouterOutput.objects.create(router=router, model_group=group, model_name="hidden")
        router = self.router("deleted-links")
        models.RouterOutput.objects.create(router=router, model_group=group, model_name="deleted", status="deleted")
        response = self.preview(group)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["affected_router_count"], 0)
        self.assertIn("NO_ROUTER", {risk["code"] for risk in response.data["risks"]})

    def test_changed_source_or_credential_ownership_rejects_preview(self):
        source, group = self.pool()
        for obj, field in ((source, "created_by"), (source.provider_account, "created_by"), (source.provider_runtime, "owner")):
            original = getattr(obj, field)
            setattr(obj, field, self.other)
            obj.save(update_fields=[field])
            response = self.preview(group)
            self.assertEqual(response.status_code, 404, response.data)
            self.assertNotIn(source.deployment_id, str(response.data))
            setattr(obj, field, original)
            obj.save(update_fields=[field])
        self.assertEqual(self.preview(group).status_code, 200)

    def test_preview_revision_input_session_and_context_fences(self):
        _, group = self.pool()
        self.assertEqual(self.preview(group, expected_revision=group.routing_revision + 1).status_code, 409)
        self.assertEqual(self.preview(group, "unsupported").status_code, 400)
        self.assertEqual(self.preview(group, sources=[{"id": str(uuid4())}]).status_code, 400)
        self.assertEqual(self.client.post(self.path, {"expected_revision": group.routing_revision, "routing_strategy": "fallback"}, format="json").status_code, 403)
        self.assertEqual(self.client.post(self.path, {}, format="json", **self.headers, HTTP_X_NEXUS_TENANT=str(uuid4())).status_code, 403)
        self.client.force_login(self.other)
        self.assertIn(self.preview(group).status_code, (401, 403))
