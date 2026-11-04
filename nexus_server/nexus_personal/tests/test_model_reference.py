from unittest.mock import patch
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.deployments.models import Deployment
from apps.gateway.model_info import source_fields
from apps.providers.runtime_services import refresh_runtime_model_offers
from .test_deployment_http import PersonalDeploymentHTTPFixture

CATALOG = {"schema": 1, "updated_at": "2026-11-04T00:00:00Z", "providers": {
    "vendor": {"Model-A": {"context_window": 128000, "max_output_tokens": 16000}},
    "vendor-cn": {"Model-A": {"context_window": 64000, "max_output_tokens": 8000}},
    "many": {f"model-{i:03}": {"context_window": 64000} for i in range(62)},
}}


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1", NEXUS_PROVIDER_MODEL_INFO_ENABLED=False)
@patch("apps.gateway.models_dev.snapshot", return_value=CATALOG)
class ModelReferenceTests(PersonalDeploymentHTTPFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.row = Deployment.objects.get(pk=self.source()["id"])
        self.path = f"/api/v1/deployments/{self.row.pk}/model-reference/"
        runtime = self.row.provider_runtime
        self.provider_path = f"/api/v1/provider-connections/{runtime.source_provider_account_id}/models/{self.row.runtime_model_offer_id}/model-reference/"

    def change(self, path, mapping, expected=None):
        return self.patch(path, {"mapping": mapping, "expected_mapping": expected})

    def test_provider_inheritance_source_override_reset_and_refresh(self, _snapshot):
        first = {"provider": "vendor", "model": "Model-A"}
        override = {"provider": "vendor-cn", "model": "Model-A"}
        result = self.change(self.provider_path, first)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertTrue(result.data["matched"])
        self.assertEqual(self.client.get(self.path).data["effective"], first)
        self.assertEqual(self.change(self.path, override).status_code, 200)
        self.row.refresh_from_db()
        self.assertEqual(source_fields(self.row, CATALOG)["max_output_tokens"]["value"], 8000)
        self.assertEqual(self.client.get(self.provider_path).data["mapping"], first)
        self.assertEqual(self.change(self.path, None, override).status_code, 200)
        refresh_runtime_model_offers(runtime=self.row.provider_runtime, actor=self.installation.owner)
        self.assertEqual(self.client.get(self.provider_path).data["mapping"], first)
        self.row.refresh_from_db()
        self.assertEqual(source_fields(self.row, CATALOG)["max_output_tokens"]["value"], 16000)
        self.assertEqual(self.change(self.provider_path, None, first).status_code, 200)
        self.assertFalse(self.client.get(self.path).data["matched"])

    def test_validation_conflicts_and_unknown_exact_identity(self, _snapshot):
        value = {"provider": "vendor", "model": "model-a"}
        result = self.change(self.path, value)
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.data["matched"], "case mismatch cannot select a different model")
        self.assertEqual(self.change(self.path, None).status_code, 409)
        self.assertEqual(self.change(self.path, {"provider": "vendor"}, value).status_code, 400)
        self.assertEqual(self.change(self.provider_path, {"provider": "a\n", "model": "b"}).status_code, 400)
        self.assertEqual(self.change(self.provider_path, {"provider": "vendor", "model": "Model-A"}).status_code, 200)
        self.assertEqual(self.change(self.provider_path, None).status_code, 409)
        self.assertEqual(self.patch(self.path, {"mapping": value}).status_code, 400)

    def test_search_pagination_and_authentication(self, _snapshot):
        base = "/api/v1/model-reference-catalog/"
        self.assertEqual([row["id"] for row in self.client.get(base, {"q": "VENDOR"}).data["results"]], ["vendor", "vendor-cn"])
        page = self.client.get(base, {"provider": "many"}).data
        self.assertEqual(len(page["results"]), 50)
        self.assertEqual(len(self.client.get(base, {"provider": "many", "offset": page["next_offset"]}).data["results"]), 12)
        self.assertEqual(self.client.get(base, {"offset": "bad"}).status_code, 400)
        anonymous = APIClient()
        for url in [base, self.path, self.provider_path]:
            self.assertIn(anonymous.get(url).status_code, (401, 403))

    def test_read_only_or_other_resource_cannot_write(self, _snapshot):
        value = {"provider": "vendor", "model": "Model-A"}
        for module, path in [("apps.deployments.model_reference", self.path), ("apps.providers.model_reference", self.provider_path)]:
            with patch(module + ".resource_access_payload", return_value={"can_read": True, "can_edit": False}):
                self.assertEqual(self.client.get(path).status_code, 200)
                self.assertEqual(self.change(path, value).status_code, 403)
            with patch(module + ".resource_access_payload", return_value={"can_read": False, "can_edit": False}):
                self.assertEqual(self.client.get(path).status_code, 403)
        missing = self.path.replace(str(self.row.pk), "00000000-0000-4000-8000-000000000001")
        self.assertEqual(self.change(missing, value).status_code, 404)
