"""Real fixed-owner context, with no collaboration administration."""
import importlib
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory, TestCase, override_settings
from rest_framework import exceptions
from rest_framework.test import APIClient
from apps.accounts.models import AccountProfile
from apps.tenancy.models import Membership, Project, Team, Tenant
from apps.tenancy import services, host
from apps.common.request_context import get_tenant_from_request
from nexus_personal import tenancy_services
from nexus_personal.services import provision_owner


@override_settings(ROOT_URLCONF="nexus_personal.urls")
class PersonalTenancyHostTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="context-owner@example.test",
                                  password="fixture-personal-context-owner-4829!")
        cls.owner = cls.row.owner
        cls.other = get_user_model().objects.create_user(username="other-context-user")

    def request(self, user=None, **headers):
        request = RequestFactory().get("/api/v1/personal/context/", **headers)
        request.user = self.owner if user is None else user
        return request

    def test_legacy_context_resolves_fixed_owner_without_membership(self):
        self.assertIs(services, tenancy_services)
        self.assertIs(services.get_tenant_from_request, get_tenant_from_request)
        self.assertEqual(Membership.objects.count(), 0)
        request = self.request()
        self.assertEqual(services.get_tenant_from_request(request), self.row.tenant)
        self.assertEqual((request.tenant_id, request.project_id),
                         (str(self.row.tenant_id), str(self.row.project_id)))

    def test_foreign_identity_context_and_revoked_owner_are_rejected(self):
        for request in (self.request(self.other), self.request(AnonymousUser()),
                        self.request(HTTP_X_NEXUS_TENANT="foreign"),
                        self.request(HTTP_X_NEXUS_PROJECT="foreign")):
            with self.assertRaises(exceptions.APIException):
                services.get_tenant_from_request(request)
        AccountProfile.objects.filter(user=self.owner).update(status="deleted")
        with self.assertRaises(exceptions.AuthenticationFailed):
            services.get_tenant_from_request(self.request())

    def test_management_symbols_and_modules_are_not_available(self):
        for name in ("create_tenant", "create_project", "create_team", "offboard_member",
                     "visible_tenants", "visible_projects", "user_can_manage_tenant"):
            with self.assertRaises(AttributeError):
                getattr(services, name)
        for kind in ("views", "serializers", "permissions", "urls"):
            with self.assertRaises(ImproperlyConfigured):
                importlib.import_module("apps.tenancy." + kind)

    def test_owner_cannot_create_or_manage_collaboration_through_legacy_urls(self):
        client = APIClient()
        client.force_login(self.owner)
        before = [model.objects.count() for model in (Tenant, Project, Team, Membership)]
        for path in ("tenants/", "teams/", "projects/", "users/search/",
                     f"projects/{self.row.project_id}/members/"):
            self.assertEqual(client.get("/api/v1/" + path).status_code, 404, path)
            self.assertEqual(client.post("/api/v1/" + path, {"name": "unexpected"}, format="json").status_code, 404, path)
        self.assertEqual([model.objects.count() for model in (Tenant, Project, Team, Membership)], before)
        response = client.get("/api/v1/personal/context/")
        self.assertEqual(response.status_code, 200, response.data)

    def test_unconfigured_context_host_is_not_an_implicit_personal_fallback(self):
        with override_settings(NEXUS_TENANCY_SERVICES_MODULE=""), self.assertRaises(ImproperlyConfigured):
            host.configured_module("services")

    def test_legacy_detail_removal_and_offboarding_cannot_mutate_personal_installation(self):
        client = APIClient()
        client.force_login(self.owner)
        models = (Tenant, Project, Team, Membership)
        before = [[(str(row.pk), row.status) for row in model.objects.order_by('pk')] for model in models]
        endpoints = (
            f'tenants/{self.row.tenant_id}/',
            f'tenants/{self.row.tenant_id}/deletion-impact/',
            f'tenants/{self.row.tenant_id}/remove/',
            f'projects/{self.row.project_id}/',
            f'projects/{self.row.project_id}/deletion-impact/',
            f'projects/{self.row.project_id}/remove/',
            f'projects/{self.row.project_id}/members/{self.owner.pk}/offboard/',
            f'teams/{self.row.project_id}/members/{self.owner.pk}/summary/',
        )
        for endpoint in endpoints:
            for method in ('get', 'post', 'patch', 'delete'):
                with self.subTest(endpoint=endpoint, method=method):
                    response = getattr(client, method)('/api/v1/' + endpoint)
                    self.assertEqual(response.status_code, 404)
        after = [[(str(row.pk), row.status) for row in model.objects.order_by('pk')] for model in models]
        self.assertEqual(after, before)
        response = client.get('/api/v1/personal/context/')
        self.assertEqual(response.status_code, 200, response.data)

    def test_mixed_provider_composition_stays_disabled_while_owned_connections_work(self):
        for name in ("apps.providers.views", "apps.providers.urls", "apps.providers.serializers"):
            with self.assertRaises(ImproperlyConfigured):
                importlib.import_module(name)
        client = APIClient()
        client.force_login(self.owner)
        response = client.get("/api/v1/provider-connections/")
        self.assertEqual(response.status_code, 200, response.data)
        for path in ("provider-pool/contributions/", "provider-pool/usages/",
                     "provider-accounts/", "provider-connections/unknown/share/"):
            self.assertEqual(client.get("/api/v1/" + path).status_code, 404, path)
            self.assertEqual(client.post("/api/v1/" + path, {}, format="json").status_code, 404, path)

    def test_personal_provider_serialization_retains_operational_prices_not_settlement(self):
        from nexus_personal.provider_serializers import PersonalProviderModelSerializer
        from apps.providers.connection_presentation import connection_serializer
        from nexus_personal.provider_serializers import PersonalProviderConnectionSerializer
        self.assertIs(connection_serializer(), PersonalProviderConnectionSerializer)
        fields = PersonalProviderModelSerializer().fields
        self.assertIn("upstream_model_id", fields)
        self.assertIn("input_price_per_1k_tokens", fields)
        self.assertIn("output_price_per_1k_tokens", fields)
        self.assertIn("image_pricing", fields)
        for name in ("gross_amount", "owner_amount", "platform_amount"):
            self.assertNotIn(name, fields)
