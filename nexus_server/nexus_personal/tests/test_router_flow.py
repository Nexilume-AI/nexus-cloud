"""Original Router HTTP contracts using Personal owner/session/CSRF and live upstream discovery."""
from tempfile import TemporaryDirectory
from uuid import uuid4
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.deployments.models import CanonicalModel, Deployment, ModelGroup
from apps.providers import runtime_services
from apps.routers.models import Router
from apps.tenancy.models import Project
from tests.router_http_guards import RouterFileFixture, RouterHTTPGuards
from .provider_http_fixture import ProviderHTTPFixture
from .test_installation import PASSWORD


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1")
class PersonalRouterFlowTests(ProviderHTTPFixture, RouterFileFixture, RouterHTTPGuards, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.installation.owner
        self.tenant = self.installation.tenant
        self.project = self.installation.project
        self.temp_dir = TemporaryDirectory(prefix="nexus-router-flow-")
        self.addCleanup(self.temp_dir.cleanup)
        configured = self.settings(NEXUS_ROUTER_STORAGE_ROOT=self.temp_dir.name)
        configured.enable()
        self.addCleanup(configured.disable)
        self.client = APIClient(enforce_csrf_checks=True)
        self.authorize_router()

    def authorize_router(self):
        self.assertTrue(self.client.login(username=self.owner.username, password=PASSWORD))
        response = self.client.get("/api/v1/public/bootstrap/")
        self.assertEqual(response.status_code, 200, response.content)
        self.csrf = self.client.cookies["csrftoken"].value

    def router_request(self, method, *args, **kwargs):
        return getattr(self.client, method)(*args, HTTP_X_CSRFTOKEN=self.csrf,
            HTTP_ORIGIN="http://testserver", **kwargs)

    def router_payload(self, response):
        return response.json()

    def assert_router_success(self, response):
        self.assertNotIn("ok", response.json())
        self.assertNotIn("error", response.json())

    def _create_router(self, name="Cost Router", strategy=Router.STRATEGY_COST,
                       router_type=Router.TYPE_EXECUTION):
        return Router.objects.create(tenant=self.tenant, project=self.project, name=name,
            strategy=strategy, router_type=router_type, created_by=self.owner)

    def _create_model_group(self, *, name, display_name):
        canonical, _ = CanonicalModel.objects.get_or_create(key=name, defaults={"display_name": display_name})
        return ModelGroup.objects.create(tenant=self.tenant, project=self.project,
            canonical_model=canonical, name=name, display_name=display_name, created_by=self.owner)

    def _make_model_group_available(self, group):
        type(self).catalog_models = [group.name]
        _, runtime = self.create("router-" + group.name)
        runtime_services.start_provider_runtime(request=self.request(), runtime_id=str(runtime.pk))
        offer = runtime.model_offers.get()
        response = self.router_request("post", "/api/v1/deployments/", {
            "source_type": "provider_runtime", "provider_runtime_id": str(runtime.pk),
            "model_offer_id": str(offer.pk), "model_group_id": str(group.pk),
            "deployment_id": "router-source-" + uuid4().hex,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(any(call[0] == "GET" for call in self.calls))
        self.assertTrue(any(call[0] == "POST" for call in self.calls))
        return Deployment.objects.get(pk=response.data["id"])

    def test_mutations_require_actual_session_csrf_and_owned_project(self):
        router = self._create_router()
        path = f"/api/v1/routers/{router.pk}/"
        self.assertEqual(self.client.patch(path, {"name": "denied"}, format="json").status_code, 403)
        other_project = Project.objects.create(tenant=self.tenant, name="foreign")
        self.assertEqual(self.router_request("get", path, HTTP_X_NEXUS_PROJECT=str(other_project.pk)).status_code, 403)
        for change in ({"created_by": self.other}, {"created_by": self.owner, "project": other_project}):
            Router.objects.filter(pk=router.pk).update(**change)
            self.assertEqual(self.router_request("get", path).status_code, 404)
            self.assertEqual(self.router_request("post", path + "deploy/").status_code, 404)
        self.client.logout()
        self.assertIn(self.client.get("/api/v1/routers/").status_code, (401, 403))
