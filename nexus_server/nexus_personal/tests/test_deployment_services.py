"""Real Provider HTTP discovery -> personal Source/Pool service and policy history."""
from uuid import uuid4
from django.test import TestCase, override_settings
from rest_framework import exceptions
from apps.deployments import services
from apps.deployments.integration import deployment_integration
from apps.deployments.models import Deployment, ModelGroup, ModelGroupDeployment
from apps.providers import runtime_services
from apps.providers.models import ProviderRuntimeAccount
from apps.tenancy.models import Project
from .provider_http_fixture import ProviderHTTPFixture


@override_settings(NEXUS_PROVIDER_ALLOW_HTTP=True, NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1")
class PersonalDeploymentServiceTests(ProviderHTTPFixture, TestCase):
    def active_runtime(self, name="upstream"):
        account, runtime = self.create(name)
        runtime_services.start_provider_runtime(request=self.request(), runtime_id=str(runtime.pk))
        runtime.refresh_from_db()
        return account, runtime, runtime.model_offers.get()

    def source(self, *, runtime, offer, name="own-source", pool="own-pool"):
        return services.create_deployment(request=self.request(), data={
            "source_type": "provider_runtime", "provider_runtime_id": runtime.pk, "model_offer_id": offer.pk,
            "deployment_id": name, "model_group_name": pool})

    def test_real_source_health_pool_revisions_rollback_disable_and_delete(self):
        _, runtime, offer = self.active_runtime()
        source = self.source(runtime=runtime, offer=offer)
        group = services.get_model_group(request=self.request(), model_group_identifier="own-pool")
        self.assertEqual(source.project_id, self.installation.project_id)
        self.assertEqual(group.created_by_id, self.installation.owner_id)
        self.assertEqual(list(services.list_deployments(request=self.request())), [source])
        self.assertEqual(list(services.list_visible_models(request=self.request())), [group])
        self.assertEqual(services.get_deployment(request=self.request(), deployment_identifier=str(source.pk)), source)
        type(self).calls.clear()
        healthy = services.run_deployment_health_check(request=self.request(), deployment_identifier=str(source.pk))
        self.assertEqual(healthy.health_status, "healthy", healthy.health_reason)
        self.assertEqual(self.calls, [("GET", "/v1/models")])
        from apps.gateway.provider_clients import OpenAICompatibleClient
        response = OpenAICompatibleClient().chat_completions(deployment=healthy,
            payload={"model": "downstream-alias", "messages": [{"role": "user", "content": "Source probe"}]})
        self.assertEqual(response.raw["choices"][0]["message"]["content"], "healthy")
        self.assertIn(("POST", "/v1/chat/completions", "personal-model"), self.calls)
        initial = group.routing_revision
        link = group.deployment_links.get()
        group = services.update_model_group_routing(request=self.request(), model_group_identifier=str(group.pk),
            data={"expected_revision": initial, "routing_strategy": "weighted", "sources": [{"id": link.pk, "weight": 7}]})
        self.assertEqual(group.routing_revision, initial + 1)
        link.refresh_from_db()
        self.assertEqual(link.weight, 7)
        with self.assertRaises(services.ModelGroupRoutingConflict):
            services.update_model_group_routing(request=self.request(), model_group_identifier=str(group.pk),
                data={"expected_revision": initial, "routing_strategy": "fallback"})
        history = services.list_model_group_routing_history(request=self.request(), model_group_identifier=str(group.pk))
        self.assertEqual(len(history), 2)
        group = services.rollback_model_group_routing(request=self.request(), model_group_identifier=str(group.pk),
            expected_revision=group.routing_revision, target_revision=initial)
        self.assertEqual(group.routing_strategy, "fallback")
        self.assertEqual(group.routing_revision, initial + 2)
        services.disable_deployment(request=self.request(), deployment_identifier=str(source.pk))
        services.delete_deployment(request=self.request(), deployment_identifier=str(source.pk))
        source.refresh_from_db(); link.refresh_from_db(); group.refresh_from_db()
        self.assertEqual(source.status, "deleted")
        self.assertEqual(link.status, "deleted")
        self.assertEqual(group.routing_revision, initial + 3)
        self.assertEqual(services.list_deployments(request=self.request()).count(), 0)
        self.assertEqual(services.list_visible_models(request=self.request()).count(), 1)

    def test_batch_failure_rolls_back_sources_pool_and_audit(self):
        _, runtime, offer = self.active_runtime()
        from apps.audit.models import AuditLog
        before = AuditLog.objects.count()
        with self.assertRaises(exceptions.ValidationError):
            services.create_model_sources_batch(request=self.request(), data={
                "origin": {"type": "provider_runtime", "provider_runtime_id": runtime.pk}, "sources": [
                    {"source_id": "first", "model_offer_id": offer.pk, "new_pool": {"name": "batch-pool"}},
                    {"source_id": "bad", "model_offer_id": uuid4(), "new_pool": {"name": "batch-pool"}}]})
        self.assertFalse(Deployment.objects.exists())
        self.assertFalse(ModelGroup.objects.exists())
        self.assertEqual(AuditLog.objects.count(), before)

    def test_owner_context_and_credential_binding_cannot_be_bypassed(self):
        account, runtime, offer = self.active_runtime()
        with self.assertRaises(exceptions.AuthenticationFailed):
            services.create_deployment(request=self.request(user=self.other), data={})
        account.created_by = self.other
        account.save(update_fields=["created_by"])
        with self.assertRaises(exceptions.ValidationError):
            self.source(runtime=runtime, offer=offer)
        account.created_by = self.installation.owner
        account.save(update_fields=["created_by"])
        ProviderRuntimeAccount.objects.filter(pk=runtime.pk).update(owner=self.other)
        with self.assertRaises(exceptions.ValidationError):
            self.source(runtime=runtime, offer=offer)
        ProviderRuntimeAccount.objects.filter(pk=runtime.pk).update(owner=self.installation.owner,
            project=Project.objects.create(tenant=self.installation.tenant, name="Foreign project"))
        with self.assertRaises(exceptions.ValidationError):
            self.source(runtime=runtime, offer=offer)
        self.assertFalse(Deployment.objects.exists())

    def test_hidden_pool_and_deleted_foreign_source_cannot_be_taken_over(self):
        _, runtime, offer = self.active_runtime()
        group = ModelGroup.objects.create(tenant=self.installation.tenant, project=self.installation.project,
            name="foreign-pool", canonical_model=offer.canonical_model, created_by=self.other)
        with self.assertRaises(exceptions.NotFound):
            self.source(runtime=runtime, offer=offer, pool=group.name)
        self.assertFalse(Deployment.objects.exists())
        source = self.source(runtime=runtime, offer=offer)
        services.delete_deployment(request=self.request(), deployment_identifier=str(source.pk))
        Deployment.objects.filter(pk=source.pk).update(created_by=self.other)
        with self.assertRaises(exceptions.NotFound):
            self.source(runtime=runtime, offer=offer)
        source.refresh_from_db()
        self.assertEqual(source.status, "deleted")
        self.assertEqual(source.created_by_id, self.other.pk)
        self.assertNotIn(group, list(services.list_visible_models(request=self.request())))

    def test_personal_commerce_requests_rejected_without_private_imports(self):
        with self.assertRaises(exceptions.ValidationError):
            services.create_deployment(request=self.request(), data={"source_type": "marketplace", "pool_contribution_id": uuid4()})
        with self.assertRaises(exceptions.ValidationError):
            services.create_model_sources_batch(request=self.request(), data={
                "origin": {"type": "marketplace_provider_runtime", "provider_runtime_id": uuid4()},
                "sources": [{"source_id": "x", "model_offer_id": uuid4(), "new_pool": {"name": "x"}}]})
        with self.assertRaises(exceptions.ValidationError):
            deployment_integration().scope_queryset_to_request_ownership(queryset=Project.objects.all(), request=self.request())
        self.assertFalse(Deployment.objects.exists())

    def test_all_nonlocal_origins_are_rejected_without_creating_sources_or_pools(self):
        from apps.audit.models import AuditLog
        before = AuditLog.objects.count()
        for value in (None, "", "manual", "unknown", [], {}):
            with self.subTest(value=value), self.assertRaises(exceptions.ValidationError) as error:
                services.create_deployment(request=self.request(), data={"source_type": value})
            self.assertEqual(error.exception.get_codes(), {"source_type": "SOURCE_ORIGIN_UNAVAILABLE"})
        self.assertFalse(Deployment.objects.exists())
        self.assertFalse(ModelGroup.objects.exists())
        self.assertEqual(AuditLog.objects.count(), before)

    def test_source_loses_access_when_its_runtime_or_credential_owner_changes(self):
        account, runtime, offer = self.active_runtime()
        source = self.source(runtime=runtime, offer=offer)
        account.created_by = self.other
        account.save(update_fields=["created_by"])
        self.assertFalse(services.list_deployments(request=self.request()).exists())
        with self.assertRaises(services.DeploymentNotFound):
            services.run_deployment_health_check(request=self.request(), deployment_identifier=str(source.pk))
        account.created_by = self.installation.owner
        account.save(update_fields=["created_by"])
        ProviderRuntimeAccount.objects.filter(pk=runtime.pk).update(owner=self.other)
        self.assertFalse(services.list_deployments(request=self.request()).exists())
        ProviderRuntimeAccount.objects.filter(pk=runtime.pk).update(owner=self.installation.owner)
        self.assertEqual(services.get_deployment(request=self.request(), deployment_identifier=str(source.pk)), source)

    def test_invalid_weight_and_no_enabled_source_leave_policy_unchanged(self):
        _, runtime, offer = self.active_runtime()
        source = self.source(runtime=runtime, offer=offer)
        link = ModelGroupDeployment.objects.get(deployment=source)
        group = link.model_group
        original_revision = group.routing_revision
        for change in ({"enabled": False}, {"weight": 0}):
            with self.assertRaises(services.ModelGroupRoutingInvalid):
                services.update_model_group_routing(request=self.request(), model_group_identifier=str(group.pk), data={
                    "expected_revision": original_revision, "routing_strategy": "weighted", "sources": [{"id": link.pk, **change}]})
        group.refresh_from_db(); link.refresh_from_db()
        self.assertEqual(group.routing_revision, original_revision)
        self.assertTrue(link.enabled)
        self.assertGreater(link.weight, 0)
