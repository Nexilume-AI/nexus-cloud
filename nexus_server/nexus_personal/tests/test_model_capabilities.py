from unittest.mock import patch
from django.test import TestCase, override_settings
from apps.deployments.models import Deployment
from .test_deployment_http import PersonalDeploymentHTTPFixture


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1", NEXUS_PROVIDER_MODEL_INFO_ENABLED=False)
@patch("apps.gateway.models_dev.snapshot", return_value={})
class PersonalModelCapabilitiesTests(PersonalDeploymentHTTPFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.row = Deployment.objects.get(pk=self.source()["id"])
        self.source_path = f"/api/v1/deployments/{self.row.pk}/model-capabilities/"
        self.provider_path = f"/api/v1/provider-connections/{self.row.provider_runtime.source_provider_account_id}/models/{self.row.runtime_model_offer_id}/model-capabilities/"

    def test_provider_and_source_overrides_are_shared_with_gateway(self, _snapshot):
        value = {"supports_reasoning": True, "reasoning_effort_levels": ["none", "low"], "default_reasoning_effort": "none"}
        result = self.patch(self.provider_path, {"override": value, "expected_override": None})
        self.assertEqual(result.status_code, 200, result.data)
        info = self.client.get(self.source_path).data["model_info"]
        self.assertEqual(info["reasoning_effort_levels"], ["none", "low"])
        self.assertEqual(info["default_reasoning_effort"], "none")
        result = self.patch(self.source_path, {"override": {"supports_reasoning": False}, "expected_override": None})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data["model_info"]["reasoning_effort_levels"], [])
        self.assertEqual(self.client.get(self.provider_path).data["override"], value)
        self.assertEqual(self.patch(self.source_path, {"override": None, "expected_override": None}).status_code, 409)
        self.assertEqual(self.patch(self.source_path, {"override": None, "expected_override": {"supports_reasoning": False}}).data["model_info"]["reasoning_effort_levels"], ["none", "low"])

    def test_shared_readonly_resources_cannot_modify_capabilities(self, _snapshot):
        for module, path in [("apps.deployments.model_reference", self.source_path), ("apps.providers.model_reference", self.provider_path)]:
            with patch(module + ".resource_access_payload", return_value={"can_read": True, "can_edit": False}):
                self.assertEqual(self.client.get(path).status_code, 200)
                self.assertEqual(self.patch(path, {"override": None, "expected_override": None}).status_code, 403)
