"""Actual personal Provider/Model migrations and fixed-owner discovery queries."""
from types import SimpleNamespace
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import FieldDoesNotExist
from django.db import IntegrityError, connection, transaction
from django.db.models import Value
from django.test import TestCase
from rest_framework import exceptions
from apps.providers.catalog import provider_catalog
from apps.providers.models import Provider, ProviderAccount, ProviderRuntimeAccount, ProviderRuntimeModelOffer, ProviderRuntimeHealthCheck
from apps.providers.health_services import record_runtime_health_check
from apps.deployments.models import CanonicalModel, Deployment, ModelGroup, ModelGroupDeployment
from apps.tenancy.models import Tenant, Project
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


class PersonalProviderCatalogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="provider-owner@example.test", password=PASSWORD)
        cls.other = get_user_model().objects.create_user(username="different-owner")
        cls.provider = Provider.objects.create(name="personal-provider")

    def request(self, user=None, **params):
        return SimpleNamespace(user=user or self.row.owner, META={}, query_params=params)

    def account(self, name, *, runtime=True, tenant=None, project=None, owner=None):
        account = ProviderAccount.objects.create(tenant=tenant or self.row.tenant, provider=self.provider,
            account_id=name, name=name, created_by=owner or self.row.owner)
        if runtime:
            ProviderRuntimeAccount.objects.create(tenant=tenant or self.row.tenant,
                project=project or self.row.project, owner=owner or self.row.owner,
                source_provider_account=account, name=name, runtime_type="direct_api")
        return account

    def visible(self, request=None, rows=None):
        policy = provider_catalog()
        request = request or self.request()
        if rows is not None:
            return policy.filter_rows(accounts=rows, request=request, tenant=self.row.tenant)
        return list(policy.filter_queryset(queryset=ProviderAccount.objects.all(), request=request, tenant=self.row.tenant))

    def test_actual_schema_excludes_commercial_tables_and_relations(self):
        self.assertEqual(len(list(apps.get_app_config("providers").get_models())), 6)
        self.assertEqual(len(list(apps.get_app_config("deployments").get_models())), 6)
        tables = connection.introspection.table_names()
        for suffix in ("providerpoolcontribution", "providerpoolusage", "providerusageaccrual", "providercapacityreservation", "providermarketplacestats"):
            self.assertNotIn("providers_" + suffix, tables)
        for model, field in ((Deployment, "marketplace_contribution"), (ProviderRuntimeHealthCheck, "contribution")):
            with self.assertRaises(FieldDoesNotExist):
                model._meta.get_field(field)

    def test_real_row_and_paginated_queries_agree_and_all_never_widens_scope(self):
        own = self.account("own")
        organization = self.account("organization")
        ProviderRuntimeAccount.objects.filter(source_provider_account=organization).update(project=None)
        self.account("missing-runtime", runtime=False)
        self.account("other-owner", owner=self.other)
        other_project = Project.objects.create(tenant=self.row.tenant, name="Other")
        self.account("other-project", project=other_project)
        other_tenant = Tenant.objects.create(name="Different", slug="different")
        self.account("other-tenant", tenant=other_tenant)
        for view in ("current", "all"):
            request = self.request(view_scope=view)
            actual = self.visible(request)
            self.assertEqual({a.pk for a in actual}, {own.pk, organization.pk})
            rows = self.visible(request, rows=list(ProviderAccount.objects.order_by("name")))
            self.assertEqual({a.pk for a in rows}, {own.pk, organization.pk})
            self.assertEqual([a.name for a in rows], sorted(a.name for a in rows))

    def test_stale_rows_and_forged_annotations_are_not_authority(self):
        account = self.account("stale")
        account.prefetched_connection_runtimes = [SimpleNamespace(project=self.row.project)]
        ProviderRuntimeAccount.objects.filter(source_provider_account=account).update(owner=self.other)
        self.assertEqual(self.visible(rows=[account]), [])
        queryset = ProviderAccount.objects.annotate(catalog_runtime_count=Value(1), catalog_project_id=Value(str(self.row.project_id)))
        self.assertEqual(list(provider_catalog().filter_queryset(queryset=queryset, request=self.request(), tenant=self.row.tenant)), [])
        ProviderRuntimeAccount.objects.filter(source_provider_account=account).update(owner=self.row.owner, status="deleted")
        self.assertEqual(self.visible(), [])

    def test_runtime_ownership_is_consistent_and_one_to_one_enforced(self):
        account = self.account("binding")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProviderRuntimeAccount.objects.create(tenant=self.row.tenant, source_provider_account=account,
                name="duplicate", runtime_type="direct_api")
        other_tenant = Tenant.objects.create(name="Different", slug="different")
        ProviderRuntimeAccount.objects.filter(source_provider_account=account).update(tenant=other_tenant)
        self.assertEqual(self.visible(), [])

    def test_source_model_and_health_records_use_actual_core_tables(self):
        account = self.account("model-provider")
        runtime = ProviderRuntimeAccount.objects.get(source_provider_account=account)
        canonical = CanonicalModel.objects.create(key="upstream-model", display_name="Model")
        offer = ProviderRuntimeModelOffer.objects.create(runtime_account=runtime, canonical_model=canonical,
            upstream_model_id="upstream-model", status="confirmed")
        source = Deployment.objects.create(tenant=self.row.tenant, project=self.row.project, provider=self.provider,
            provider_account=account, provider_runtime=runtime, runtime_model_offer=offer, canonical_model=canonical,
            deployment_id="personal-source", upstream_model_id="upstream-model", created_by=self.row.owner)
        group = ModelGroup.objects.create(tenant=self.row.tenant, project=self.row.project, canonical_model=canonical,
            name="pool", created_by=self.row.owner)
        ModelGroupDeployment.objects.create(model_group=group, deployment=source)
        check = record_runtime_health_check(runtime=runtime, status="active", latency_ms=12)
        self.assertEqual(check.tenant_id, self.row.tenant_id)
        self.assertEqual(check.latency_ms, 12)
        self.assertEqual(group.deployment_links.get().deployment.runtime_model_offer_id, offer.pk)
        self.assertEqual(runtime.primary_model_offer.pk, offer.pk)

    def test_other_owner_is_denied_before_provider_query(self):
        with self.assertRaises(exceptions.AuthenticationFailed):
            provider_catalog().filter_rows(accounts=[], request=self.request(user=self.other), tenant=self.row.tenant)

    def test_wrong_context_and_invalid_view_do_not_bypass_identity(self):
        other_tenant = Tenant.objects.create(name="Different", slug="different")
        with self.assertRaises(exceptions.NotFound):
            provider_catalog().filter_rows(accounts=[], request=self.request(), tenant=other_tenant)
        with self.assertRaises(exceptions.ValidationError):
            provider_catalog().filter_rows(accounts=[], request=self.request(view_scope="everything"), tenant=self.row.tenant)
        request = self.request()
        request.META["HTTP_X_NEXUS_PROJECT"] = "other-project"
        with self.assertRaises(exceptions.PermissionDenied):
            provider_catalog().filter_rows(accounts=[], request=request, tenant=self.row.tenant)
