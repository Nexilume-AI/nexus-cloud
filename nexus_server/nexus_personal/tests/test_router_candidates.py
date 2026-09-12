"""Real Provider -> Source -> Execution -> Aggregation candidate lifecycle."""
from datetime import timedelta
from uuid import uuid4
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.test import APIClient
from apps.deployments.candidates import model_group_deployment_candidates, resolve_model_source_deployment
from apps.deployments.models import Deployment, ModelGroup
from apps.providers import runtime_services
from apps.routers import services
from apps.routers.models import Router, RouterOutput
from apps.tenancy.models import Project, Tenant
from .provider_http_fixture import ProviderHTTPFixture
from . import test_deployment_http as source_http


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1")
class PersonalRouterCandidateTests(ProviderHTTPFixture, TestCase):
    source = source_http.PersonalDeploymentHTTPTests.source
    post = source_http.PersonalDeploymentHTTPTests.post

    def setUp(self):
        super().setUp()
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.installation.owner)
        self.client.get("/api/v1/public/bootstrap/")
        self.headers = {"HTTP_X_CSRFTOKEN": self.client.cookies["csrftoken"].value, "HTTP_ORIGIN": "http://testserver"}
        self.source_row = Deployment.objects.get(pk=self.source()["id"])
        self.group = ModelGroup.objects.get()
        self.execution = services.create_router(request=self.request(), name="execution", model_group_ids=[self.group.pk])
        self.aggregate = services.create_router(request=self.request(), name="aggregate", router_type="aggregation")

    def rows(self):
        return services.list_aggregation_candidates(request=self.request(), router_id=str(self.aggregate.pk))

    def deploy_execution(self):
        services.deploy_router(request=self.request(), router_id=str(self.execution.pk))
        return self.execution.outputs.get()

    def add(self, output, name="my-model"):
        return services.add_router_child_binding(request=self.request(), router_id=str(self.aggregate.pk),
            data={"child_output_id": output.pk, "exposed_model_name": name})

    def test_real_candidate_binding_deployment_and_removal(self):
        self.assertEqual(self.rows()[0]["code"], "EXECUTION_ROUTER_NOT_DEPLOYED")
        output = self.deploy_execution()
        row = self.rows()[0]
        self.assertTrue(row["available"])
        self.assertEqual(row["output_id"], str(output.pk))
        candidates = model_group_deployment_candidates(tenant=self.installation.tenant, group=self.group)
        self.assertEqual([item.pk for item in candidates], [self.source_row.pk])
        self.assertEqual(candidates[0]._nexus_selected_model_group_id, str(self.group.pk))
        self.assertEqual(candidates[0]._nexus_consumer_source_id, str(self.source_row.pk))
        binding = self.add(output)
        self.assertEqual(self.rows()[0]["code"], "EXECUTION_ROUTER_ALREADY_MAPPED")
        self.assertEqual(services.deploy_router(request=self.request(), router_id=str(self.aggregate.pk)).status, "active")
        services.remove_router_child_binding(request=self.request(), router_id=str(self.aggregate.pk), binding_id=str(binding.pk))
        self.assertTrue(self.rows()[0]["available"])

    def test_upstream_http_failure_stop_and_recovery_preserve_mapping(self):
        output = self.deploy_execution()
        binding = self.add(output)
        runtime = self.source_row.provider_runtime
        type(self).probe_status = 503
        type(self).calls.clear()
        with self.assertRaises(runtime_services.ProviderRuntimeError):
            runtime_services.refresh_provider_runtime_models(request=self.request(), runtime_id=str(runtime.pk))
        self.assertEqual(sum(call[0] == "POST" for call in self.calls), 1)
        offer = runtime.model_offers.get()
        self.assertEqual(offer.health_status, "degraded")
        self.assertEqual(offer.metadata["health_probe"]["state"], "pending")
        self.assertEqual(self.rows()[0]["code"], "EXECUTION_ROUTER_ALREADY_MAPPED")
        # Age the last definitive observation, then perform another real probe.
        # A transient failure must not turn the existing grace period into forever.
        offer.metadata["health_probe"]["last_definitive_at"] = (timezone.now() - timedelta(minutes=6)).isoformat()
        offer.save(update_fields=["metadata"])
        with self.assertRaises(runtime_services.ProviderRuntimeError):
            runtime_services.refresh_provider_runtime_models(request=self.request(), runtime_id=str(runtime.pk))
        self.assertEqual(runtime.model_offers.get().health_status, "unknown")
        self.assertEqual(self.rows()[0]["code"], "EXECUTION_MODEL_UNAVAILABLE")
        self.assertEqual(self.aggregate.child_bindings.get().pk, binding.pk)
        type(self).probe_status = 200
        runtime_services.refresh_provider_runtime_models(request=self.request(), runtime_id=str(runtime.pk))
        self.assertEqual(self.rows()[0]["code"], "EXECUTION_ROUTER_ALREADY_MAPPED")
        runtime_services.stop_provider_runtime(request=self.request(), runtime_id=str(runtime.pk))
        self.assertEqual(self.rows()[0]["code"], "EXECUTION_MODEL_UNAVAILABLE")
        runtime_services.start_provider_runtime(request=self.request(), runtime_id=str(runtime.pk))
        self.assertEqual(self.rows()[0]["code"], "EXECUTION_ROUTER_ALREADY_MAPPED")
        self.assertEqual(self.execution.outputs.get().pk, output.pk)

    def test_one_to_one_mapping_and_revalidation_before_binding(self):
        output = self.deploy_execution()
        second = services.create_router(request=self.request(), name="second", model_group_ids=[self.group.pk])
        services.deploy_router(request=self.request(), router_id=str(second.pk))
        self.add(output)
        with self.assertRaises(exceptions.ValidationError):
            self.add(output, "another-name")
        with self.assertRaises(exceptions.ValidationError):
            self.add(second.outputs.get(), "my-model")
        with self.assertRaises(exceptions.ValidationError):
            self.add(type("OutputId", (), {"pk": uuid4()})())
        self.assertEqual(self.aggregate.child_bindings.count(), 1)
        self.source_row.runtime_model_offer.status = "disabled"
        self.source_row.runtime_model_offer.save(update_fields=["status"])
        with self.assertRaises(exceptions.ValidationError):
            self.add(second.outputs.get(), "second-model")
        self.assertEqual(self.aggregate.child_bindings.count(), 1)

    def test_foreign_router_and_foreign_pool_names_are_not_disclosed(self):
        output = self.deploy_execution()
        Router.objects.filter(pk=self.execution.pk).update(created_by=self.other)
        self.assertEqual(self.rows(), [])
        with self.assertRaises(exceptions.ValidationError):
            self.add(output)
        Router.objects.filter(pk=self.execution.pk).update(created_by=self.installation.owner)
        ModelGroup.objects.filter(pk=self.group.pk).update(created_by=self.other, name="private-pool-marker")
        rows = self.rows()
        self.assertEqual(rows[0]["models"], [])
        self.assertNotIn("private-pool-marker", str(rows))
        self.assertEqual(rows[0]["code"], "EXECUTION_OUTPUT_UNRESOLVED")
        self.assertEqual(model_group_deployment_candidates(tenant=self.installation.tenant, group=self.group), [])
        with self.assertRaises(exceptions.APIException):
            services.list_aggregation_candidates(request=self.request(self.other), router_id=str(self.aggregate.pk))

    def test_source_runtime_offer_credential_and_quota_changes_are_current(self):
        self.deploy_execution()
        source = self.source_row
        for obj, field, bad in ((source, "created_by", self.other), (source.provider_runtime, "owner", self.other),
                                (source.provider_account, "created_by", self.other),
                                (source.runtime_model_offer, "health_status", "unknown"),
                                (source, "health_status", "unhealthy")):
            original = getattr(obj, field)
            setattr(obj, field, bad)
            obj.save(update_fields=[field])
            self.assertIsNone(resolve_model_source_deployment(tenant=self.installation.tenant, source=source))
            self.assertEqual(self.rows()[0]["code"], "EXECUTION_MODEL_UNAVAILABLE")
            setattr(obj, field, original)
            obj.save(update_fields=[field])
        account = source.provider_account
        account.quota_status = "exhausted"
        account.quota_reset_at = timezone.now() + timedelta(hours=1)
        account.save(update_fields=["quota_status", "quota_reset_at"])
        self.assertFalse(self.rows()[0]["available"])
        account.quota_reset_at = timezone.now() - timedelta(seconds=1)
        account.save(update_fields=["quota_reset_at"])
        self.assertTrue(self.rows()[0]["available"])

    def test_pool_scope_and_output_state_cannot_be_forged(self):
        output = self.deploy_execution()
        for field, value in (("enabled", False), ("model_group", None), ("status", "deleted")):
            RouterOutput.objects.filter(pk=output.pk).update(**{field: value})
            with self.assertRaises(exceptions.ValidationError):
                self.add(output)
            RouterOutput.objects.filter(pk=output.pk).update(enabled=True, model_group=self.group, status="active")
        project = Project.objects.create(tenant=self.installation.tenant, name="outside")
        Deployment.objects.filter(pk=self.source_row.pk).update(project=project)
        self.assertFalse(self.rows()[0]["available"])
        with self.assertRaises(exceptions.APIException):
            model_group_deployment_candidates(tenant=Tenant.objects.create(name="other"), group=self.group)
