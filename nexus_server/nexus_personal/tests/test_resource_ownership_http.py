"""Actual personal resource boundaries, independent of collaboration grants."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.agents.models import Agent
from apps.datasets.models import Dataset
from apps.routers.models import Router
from apps.tenancy.models import Project, Tenant
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


class PersonalResourceOwnershipHTTPTests(TestCase):
    resources = (("agents", Agent), ("datasets", Dataset), ("routers", Router))

    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="resource-owner@example.test", password=PASSWORD)
        cls.token = Token.objects.create(user=cls.row.owner)
        cls.other = get_user_model().objects.create_user(username="other-resource-owner")
        cls.foreign_tenant = Tenant.objects.create(name="Foreign", slug="resource-foreign")
        cls.foreign_project = Project.objects.create(tenant=cls.row.tenant, name="Not installed context")
        cls.external_project = Project.objects.create(tenant=cls.foreign_tenant, name="External context")

    def setUp(self):
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.token.key)

    def create(self, kind, name, **headers):
        response = self.client.post(f"/api/v1/{kind}/", {"name": name}, format="json", **headers)
        self.assertEqual(response.status_code, 201, response.json())
        return response.data

    def rows(self, kind, scope):
        response = self.client.get(f"/api/v1/{kind}/", {"view_scope": scope})
        self.assertEqual(response.status_code, 200, response.data)
        rows = response.data
        self.assertIsInstance(rows, list)
        return rows

    def test_actual_create_binds_installed_owner_and_context_with_or_without_headers(self):
        for kind, model in self.resources:
            for headers in ({}, {"HTTP_X_NEXUS_TENANT": str(self.row.tenant_id),
                                 "HTTP_X_NEXUS_PROJECT": str(self.row.project_id)}):
                with self.subTest(kind=kind, explicit_context=bool(headers)):
                    data = self.create(kind, f"personal-resource-{int(bool(headers))}", **headers)
                    resource = model.objects.get(pk=data["id"])
                    self.assertEqual(resource.created_by_id, self.row.owner_id)
                    self.assertEqual(resource.tenant_id, self.row.tenant_id)
                    self.assertEqual(resource.project_id, self.row.project_id)
                    self.assertEqual(data["project_id"], str(self.row.project_id))

    def test_forged_creation_scope_is_rejected_without_partial_rows(self):
        for kind, model in self.resources:
            with self.subTest(kind=kind):
                count = model.objects.count()
                for ownership in ({"scope": "organization"},
                                  {"scope": "project", "project_id": str(self.foreign_project.pk)},
                                  {"scope": "project", "project_id": str(self.external_project.pk)}):
                    response = self.client.post(f"/api/v1/{kind}/",
                        {"name": "rejected-resource", "ownership": ownership}, format="json")
                    self.assertEqual(response.status_code, 400, response.data)
                for headers in ({"HTTP_X_NEXUS_PROJECT": str(self.foreign_project.pk)},
                                {"HTTP_X_NEXUS_TENANT": str(self.foreign_tenant.pk)}):
                    response = self.client.post(f"/api/v1/{kind}/", {"name": "rejected-context"},
                                                format="json", **headers)
                    self.assertEqual(response.status_code, 403, response.json())
                self.assertEqual(model.objects.count(), count)

    def test_all_and_current_catalogs_never_reveal_foreign_owner_project_or_tenant(self):
        for kind, model in self.resources:
            with self.subTest(kind=kind):
                owned = self.create(kind, "visible-resource")
                hidden = []
                for changes in ({"created_by": self.other}, {"project": self.foreign_project},
                                {"tenant": self.foreign_tenant, "project": self.external_project}):
                    values = dict(name="hidden-resource-marker", tenant=self.row.tenant,
                                  project=self.row.project, created_by=self.row.owner)
                    hidden.append(model.objects.create(**{**values, **changes}))
                for scope in ("current", "all"):
                    rows = self.rows(kind, scope)
                    self.assertEqual([item["id"] for item in rows], [owned["id"]])
                    self.assertNotIn("hidden-resource-marker", str(rows))
                for resource in hidden:
                    response = self.client.get(f"/api/v1/{kind}/{resource.pk}/")
                    self.assertEqual(response.status_code, 404, response.data)
                    self.assertNotIn("hidden-resource-marker", str(response.data))

    def test_ownership_changes_and_revoked_owner_take_effect_on_existing_client(self):
        for kind, model in self.resources:
            with self.subTest(kind=kind):
                data = self.create(kind, "initially-visible")
                model.objects.filter(pk=data["id"]).update(created_by=self.other)
                self.assertEqual(self.client.get(f"/api/v1/{kind}/{data['id']}/").status_code, 404)
                self.assertEqual(self.rows(kind, "all"), [])
        self.token.delete()
        for kind, _ in self.resources:
            response = self.client.get(f"/api/v1/{kind}/")
            self.assertEqual(response.status_code, 401, response.json())
        get_user_model().objects.filter(pk=self.row.owner_id).update(is_active=False)
        for kind, _ in self.resources:
            response = self.client.get(f"/api/v1/{kind}/")
            self.assertEqual(response.status_code, 503, response.json())
            self.assertEqual(response.json()["code"], "PERSONAL_SETUP_REQUIRED")
