"""Real personal Router management; Gateway execution is not composed yet."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import uuid4
from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework import exceptions
from rest_framework.test import APIClient
from apps.common.resource_limits import capability_state
from apps.deployments.models import Deployment, ModelGroup
from apps.routers.models import Router, RouterModelGroupBinding
from apps.routers import services
from apps.tenancy.models import Project
from nexus_personal.resource_limits import PersonalCapacityExceeded
from .provider_http_fixture import ProviderHTTPFixture
from . import test_deployment_http as source_http


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1")
class PersonalRouterServiceTests(ProviderHTTPFixture, TestCase):
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

    def create_router(self, name="router", **kwargs):
        return services.create_router(request=self.request(), name=name, model_group_ids=[self.group.pk], **kwargs)

    def test_real_execution_create_bind_deploy_update_and_delete(self):
        router = self.create_router()
        self.assertEqual(router.project_id, self.installation.project_id)
        self.assertEqual(router.created_by_id, self.installation.owner_id)
        self.assertEqual(router.model_group_bindings.get().model_group_id, self.group.pk)
        self.assertEqual(router.outputs.get().model_group_id, self.group.pk)
        self.assertEqual(list(services.list_routers(request=self.request())), [router])
        deployed = services.deploy_router(request=self.request(), router_id=str(router.pk))
        self.assertEqual(deployed.status, "active")
        self.assertEqual(deployed.endpoint_url, f"/api/v1/routers/{router.pk}/invoke/")
        router.refresh_from_db()
        self.assertEqual(router.status, "deployed")
        self.assertEqual(services.router_output_model_names(router=router), [self.group.name])
        services.update_router(request=self.request(), router_id=str(router.pk), data={"name": "renamed", "strategy": "best_health_pool"})
        output = router.outputs.get()
        services.update_router_output(request=self.request(), router_id=str(router.pk), output_id=str(output.pk), data={"model_name": "my-model"})
        self.assertEqual(services.router_output_model_names(router=router), ["my-model"])
        services.delete_router(request=self.request(), router_id=str(router.pk))
        router.refresh_from_db()
        self.assertEqual(router.status, "deleted")
        self.assertFalse(router.outputs.exclude(status="deleted").exists())
        self.assertFalse(router.deployments.exclude(status="deleted").exists())
        self.assertEqual(list(services.list_routers(request=self.request())), [])

    def test_current_owner_context_and_stale_router_cannot_grant_access(self):
        router = self.create_router()
        Router.objects.filter(pk=router.pk).update(created_by=self.other)
        self.assertFalse(services.can_manage_router(user=self.installation.owner, router=router))
        self.assertFalse(services.can_use_router(user=self.installation.owner, router=router))
        with self.assertRaises(services.RouterNotFound):
            services.update_router(request=self.request(), router_id=str(router.pk), data={"name": "takeover"})
        forged = SimpleNamespace(pk=self.installation.owner_id, id=self.installation.owner_id, is_authenticated=True, is_api_key_principal=True)
        self.assertFalse(services.can_use_router(user=forged, router=router))
        with self.assertRaises(exceptions.APIException):
            services.list_routers(request=self.request(self.other))
        request = self.request()
        request.META["HTTP_X_NEXUS_PROJECT"] = str(uuid4())
        with self.assertRaises(exceptions.APIException):
            services.list_routers(request=request)

    def test_foreign_pools_and_legacy_bindings_are_rejected_before_writes(self):
        foreign_project = Project.objects.create(tenant=self.installation.tenant, name="foreign")
        for changes in ({"created_by": self.other}, {"project": foreign_project}):
            ModelGroup.objects.filter(pk=self.group.pk).update(**changes)
            with self.assertRaises(exceptions.ValidationError):
                self.create_router()
            self.assertFalse(Router.objects.exists())
            ModelGroup.objects.filter(pk=self.group.pk).update(created_by=self.installation.owner, project=self.installation.project)
        router = self.create_router()
        with self.assertRaises(exceptions.ValidationError):
            services.bind_model_groups(request=self.request(), router_id=str(router.pk), providers=["hidden:legacy"])
        self.assertEqual(router.model_group_bindings.count(), 1)

    def test_redeploy_revalidates_current_source_credentials(self):
        router = self.create_router()
        account = self.source_row.provider_account
        account.created_by = self.other
        account.save(update_fields=["created_by"])
        with self.assertRaises(exceptions.NotFound):
            services.deploy_router(request=self.request(), router_id=str(router.pk))
        self.assertFalse(router.deployments.exists())
        router.refresh_from_db()
        self.assertEqual(router.status, "draft")
        account.created_by = self.installation.owner
        account.save(update_fields=["created_by"])
        self.assertEqual(services.deploy_router(request=self.request(), router_id=str(router.pk)).status, "active")
        RouterModelGroupBinding.objects.filter(router=router).update(model_group=None)
        with self.assertRaises(exceptions.ValidationError):
            services.deploy_router(request=self.request(), router_id=str(router.pk))

    def test_execution_and_aggregation_capacity_count_real_rows_separately(self):
        with override_settings(NEXUS_PERSONAL_MODEL_LIMITS={"models.execution_routers": 1, "models.aggregation_routers": 1}):
            router = self.create_router()
            with self.assertRaises(PersonalCapacityExceeded):
                self.create_router("too-many")
            aggregate = services.create_router(request=self.request(), name="aggregate", router_type="aggregation")
            for code in ("models.execution_routers", "models.aggregation_routers"):
                self.assertEqual(capability_state(tenant=router.tenant, code=code)["used"], 1)
            with self.assertRaises(exceptions.ValidationError):
                services.deploy_router(request=self.request(), router_id=str(aggregate.pk))
            services.delete_router(request=self.request(), router_id=str(router.pk))
            self.assertEqual(capability_state(tenant=router.tenant, code="models.execution_routers")["used"], 0)
            self.create_router("replacement")

    def test_custom_policy_storage_validation_and_deployment(self):
        router = self.create_router()
        with TemporaryDirectory(prefix="nexus-router-policy-") as folder, override_settings(NEXUS_ROUTER_STORAGE_ROOT=folder):
            upload = SimpleUploadedFile("router.py", b"def route(request, candidates, context):\n    return candidates[0]\n")
            version = services.upload_router_file(request=self.request(), router_id=str(router.pk), uploaded_file=upload)
            self.assertTrue((Path(folder) / version.router_file_path).is_file())
            self.assertIn("return candidates[0]", services.get_router_source(request=self.request(), router_id=str(router.pk))["source"])
            services.deploy_router(request=self.request(), router_id=str(router.pk))
            services.set_policy(request=self.request(), router_id=str(router.pk), strategy="custom")
            with self.assertRaises(exceptions.ValidationError):
                services.upload_router_file(request=self.request(), router_id=str(router.pk), uploaded_file=SimpleUploadedFile("router.py", b"async def route(a,b,c): pass"))
            version.router_file_path = "../outside.py"
            with self.assertRaises(exceptions.ValidationError):
                services.safe_router_version_path(version=version)

    def test_undeployed_credentials_and_commerce_never_fake_success(self):
        router = self.create_router()
        with self.assertRaises(exceptions.ValidationError):
            services.set_pricing(request=self.request(), router_id=str(router.pk), plan_id="free")
        with self.assertRaisesMessage(exceptions.NotFound, "Router is not deployed."):
            services.export_router_credentials(request=self.request(), router_id=str(router.pk))
        with override_settings(NEXUS_ROUTER_INTEGRATION=""), self.assertRaises(ImproperlyConfigured):
            services.list_routers(request=self.request())
