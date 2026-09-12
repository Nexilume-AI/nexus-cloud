"""Authenticated Router HTTP management through actual Provider/Pool records."""
from tempfile import TemporaryDirectory
from uuid import uuid4
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework import exceptions
from rest_framework.test import APIClient
from apps.deployments.models import Deployment, ModelGroup
from apps.routers.models import Router
from nexus_personal.router_serializers import RouterSerializer
from .provider_http_fixture import ProviderHTTPFixture
from . import test_deployment_http as source_http


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1")
class PersonalRouterHTTPTests(ProviderHTTPFixture, TestCase):
    source = source_http.PersonalDeploymentHTTPTests.source
    post = source_http.PersonalDeploymentHTTPTests.post
    patch = source_http.PersonalDeploymentHTTPTests.patch

    def setUp(self):
        super().setUp()
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.installation.owner)
        self.client.get("/api/v1/public/bootstrap/")
        self.headers = {"HTTP_X_CSRFTOKEN": self.client.cookies["csrftoken"].value, "HTTP_ORIGIN": "http://testserver"}
        self.source_row = Deployment.objects.get(pk=self.source()["id"])
        self.group = ModelGroup.objects.get()

    def create_router(self, name="execution", aggregate=False):
        response = self.post("/api/v1/routers/", {"name": name, "router_type": "aggregation" if aggregate else "execution",
            "model_group_ids": [] if aggregate else [str(self.group.pk)]})
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["ownership"]["project_id"], str(self.installation.project_id))
        return response.data, f"/api/v1/routers/{response.data['id']}/"

    def test_execution_management_upload_policy_and_delete(self):
        router, path = self.create_router()
        self.assertEqual(router["model_group_ids"], [str(self.group.pk)])
        self.assertEqual(self.client.get(path).data["outputs"][0]["model_group_id"], str(self.group.pk))
        groups = self.post(path + "model-groups/", {"model_group_ids": [str(self.group.pk)]})
        self.assertEqual(groups.status_code, 200, groups.data)
        outputs = self.client.get(path + "outputs/")
        self.assertEqual(outputs.status_code, 200, outputs.data)
        output_path = path + f"outputs/{outputs.data[0]['id']}/"
        changed = self.patch(output_path, {"model_name": "my-model"})
        self.assertEqual(changed.status_code, 200, changed.data)
        self.assertEqual(self.post(path + "deploy/").status_code, 201)
        self.assertEqual(self.patch(path, {"name": "renamed"}).status_code, 200)
        policy = self.post(path + "policy/", {"strategy": "best_health_pool"})
        self.assertEqual(policy.status_code, 200, policy.data)
        self.assertEqual(policy.data["output_models"], ["my-model"])
        with TemporaryDirectory(prefix="nexus-router-http-") as folder, override_settings(NEXUS_ROUTER_STORAGE_ROOT=folder):
            upload = self.client.post(path + "upload/", {"file": SimpleUploadedFile("router.py", b"def route(request, candidates, context): return candidates[0]\n")}, format="multipart", **self.headers)
            self.assertEqual(upload.status_code, 201, upload.data)
            self.assertNotIn("router_file_path", upload.data)
            self.assertIn("def route", self.client.get(path + "source/").data["source"])
            self.assertEqual(self.post(path + "deploy/").status_code, 201)
            self.assertEqual(self.post(path + "policy/", {"strategy": "custom"}).status_code, 200)
        self.assertEqual(self.client.delete(path, **self.headers).status_code, 204)
        self.assertEqual(self.client.get(path).status_code, 404)

    def test_aggregation_candidate_mapping_update_and_cleanup(self):
        _, execution = self.create_router()
        self.assertEqual(self.post(execution + "deploy/").status_code, 201)
        _, aggregate = self.create_router("aggregate", aggregate=True)
        candidates = self.client.get(aggregate + "aggregation-candidates/")
        self.assertEqual(candidates.status_code, 200, candidates.data)
        self.assertTrue(candidates.data[0]["available"])
        data = {"child_output_id": candidates.data[0]["output_id"], "exposed_model_name": "my-model"}
        binding = self.post(aggregate + "child-bindings/", data)
        self.assertEqual(binding.status_code, 201, binding.data)
        self.assertEqual(binding.data["child_output"]["model_group_id"], str(self.group.pk))
        self.assertEqual(self.post(aggregate + "child-bindings/", {**data, "exposed_model_name": "another"}).status_code, 400)
        path = aggregate + f"child-bindings/{binding.data['id']}/"
        self.assertEqual(self.patch(path, {"exposed_model_name": "renamed-model"}).status_code, 200)
        self.assertEqual(self.post(aggregate + "deploy/").status_code, 201)
        self.assertEqual(self.client.get(aggregate).data["output_models"], ["renamed-model"])
        self.assertEqual(self.client.delete(path, **self.headers).status_code, 204)
        self.assertEqual(self.client.get(aggregate + "child-bindings/").data, [])

    def test_stale_nested_ownership_is_redacted_but_local_mapping_can_be_removed(self):
        execution_data, execution = self.create_router()
        self.post(execution + "deploy/")
        _, aggregate = self.create_router("aggregate", aggregate=True)
        binding = self.post(aggregate + "child-bindings/", {"child_output_id": execution_data["outputs"][0]["id"], "exposed_model_name": "local-alias"})
        self.assertEqual(binding.status_code, 201, binding.data)
        Router.objects.filter(pk=execution_data["id"]).update(created_by=self.other, name="foreign-router-marker")
        response = self.client.get(aggregate)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data["child_bindings"][0]["child_output"])
        self.assertNotIn("foreign-router-marker", str(response.data))
        self.assertEqual(self.client.get(execution).status_code, 404)
        Router.objects.filter(pk=execution_data["id"]).update(created_by=self.installation.owner)
        ModelGroup.objects.filter(pk=self.group.pk).update(created_by=self.other, name="foreign-pool-marker")
        for path in (execution, execution + "outputs/", aggregate, aggregate + "child-bindings/"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertNotIn("foreign-pool-marker", str(response.data))
        self.assertEqual(self.client.get(execution).data["model_group_ids"], [])
        self.assertEqual(self.client.delete(aggregate + f"child-bindings/{binding.data['id']}/", **self.headers).status_code, 204)

    def test_session_csrf_context_and_unknown_fields(self):
        data, path = self.create_router()
        self.assertEqual(self.client.delete(path).status_code, 403)
        self.assertIn(APIClient().get(path).status_code, (401, 403))
        self.assertEqual(self.client.get(path, HTTP_X_NEXUS_PROJECT=str(uuid4())).status_code, 403)
        for body in ({"name": "bad", "api_key": "unsupported"}, {"name": "bad", "pricing_json": {}},
                     {"name": "bad", "ownership": {"scope": "project", "project_id": str(uuid4())}}):
            self.assertEqual(self.post("/api/v1/routers/", body).status_code, 400)
        self.assertEqual(self.patch(path, {"router_type": "aggregation"}).status_code, 400)
        self.client.force_login(self.other)
        self.assertIn(self.client.get(path).status_code, (401, 403))
        self.assertEqual(Router.objects.get(pk=data["id"]).status, "draft")

    def test_search_cursor_scoping_and_context_free_serialization_rejected(self):
        for name in ("r-a", "r-b", "r-c"):
            self.create_router(name)
        first = self.client.get("/api/v1/routers/", {"limit": 1, "q": "r-"})
        self.assertEqual(first.status_code, 200, first.data)
        self.assertTrue(first.data["has_more"])
        second = self.client.get("/api/v1/routers/", {"limit": 1, "q": "r-", "cursor": first.data["next_cursor"]})
        self.assertEqual(second.status_code, 200, second.data)
        self.assertNotEqual(first.data["items"][0]["id"], second.data["items"][0]["id"])
        self.assertEqual(self.client.get("/api/v1/routers/", {"limit": 1, "q": "changed", "cursor": first.data["next_cursor"]}).status_code, 400)
        self.assertEqual(self.client.get("/api/v1/routers/", {"q": "absent"}).data, [])
        with self.assertRaises(exceptions.APIException):
            RouterSerializer(Router.objects.first()).data

    def test_commerce_and_uncomposed_test_endpoint_are_not_mounted(self):
        _, path = self.create_router()
        for suffix in ("pricing/", "provider-preferences/", "test/"):
            self.assertEqual(self.post(path + suffix).status_code, 404)
        self.assertEqual(self.post(path + "export-credentials/").status_code, 404)
        self.assertEqual(self.post(path + "invoke/").status_code, 400)
