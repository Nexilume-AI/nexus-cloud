"""Personal context policy checks; full resource migrations/E2E remain required."""
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import exceptions

from apps.common import resource_catalog as catalog
from apps.tenancy.models import Tenant, Project
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


class PersonalCatalogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="owner@example.test", password=PASSWORD)
        cls.other = get_user_model().objects.create_user(username="other")

    def request(self, **kwargs):
        return SimpleNamespace(user=self.row.owner, META={}, query_params={}, **kwargs)

    def test_creation_binds_real_project_without_membership_or_iam(self):
        for ownership in (None, {"scope": "project", "project_id": str(self.row.project_id)}):
            project, inferred = catalog.resolve_ownership_project(request=self.request(), tenant=self.row.tenant, ownership=ownership)
            self.assertEqual(project.pk, self.row.project_id)
            self.assertEqual(inferred, ownership is None)

    def test_organization_and_other_project_inputs_cannot_widen_scope(self):
        for ownership in ({"scope": "organization"}, {"scope": "project", "project_id": "another"}):
            with self.assertRaises(exceptions.ValidationError):
                catalog.resolve_ownership_project(request=self.request(), tenant=self.row.tenant, ownership=ownership)

    def test_other_owner_and_tenant_are_denied(self):
        other_tenant = Tenant.objects.create(name="Other", slug="other")
        with self.assertRaises(exceptions.NotFound):
            catalog.resolve_ownership_project(request=self.request(), tenant=other_tenant, ownership=None)
        request = self.request()
        request.user = self.other
        with self.assertRaises(exceptions.AuthenticationFailed):
            catalog.resolve_ownership_project(request=request, tenant=self.row.tenant, ownership=None)

    def test_wrong_queryset_model_cannot_be_exposed_as_dataset(self):
        with self.assertRaises(exceptions.ValidationError):
            catalog.discoverable_resource_queryset(Project.objects.all(), request=self.request(),
                tenant=self.row.tenant, resource_type="dataset")

    def test_unsupported_catalog_never_gets_unfiltered_queryset(self):
        for kind in ("provider_connection", "wallet", "role"):
            with self.assertRaises(exceptions.ValidationError):
                catalog.discoverable_resource_queryset(Project.objects.all(), request=self.request(),
                    tenant=self.row.tenant, resource_type=kind)

    def test_view_scope_is_validated(self):
        request = self.request()
        request.query_params = {"view_scope": "grant-everything"}
        with self.assertRaises(exceptions.ValidationError):
            catalog.discoverable_resource_queryset(Project.objects.all(), request=request,
                tenant=self.row.tenant, resource_type="dataset")

    def test_resource_policy_rechecks_current_owner_project_and_creator(self):
        # This is a policy unit test, not a claim that Dataset migrations or its
        # production endpoints can yet run without Enterprise dependencies.
        obj = SimpleNamespace(pk="resource", tenant_id=self.row.tenant_id, project=self.row.project,
                              created_by_id=self.row.owner_id)
        with patch("nexus_personal.resource_catalog.resolve_catalog_resource", return_value=obj):
            result = catalog.resource_context_payload(request=self.request(), resource_type="dataset", obj=obj)
            self.assertEqual(result["access"]["sources"], ["personal_owner"])
            self.assertEqual(result["ownership"]["project_id"], str(self.row.project_id))
            obj.created_by_id = self.other.pk
            with self.assertRaises(exceptions.NotFound):
                catalog.resource_context_payload(request=self.request(), resource_type="dataset", obj=obj)

    def test_anonymous_request_never_gets_resource_metadata(self):
        obj = SimpleNamespace(tenant_id=self.row.tenant_id)
        with self.assertRaises(exceptions.NotAuthenticated):
            catalog.resource_context_payload(request=None, resource_type="dataset", obj=obj)
