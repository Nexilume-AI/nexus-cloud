from unittest.mock import patch
from django.test import TestCase, override_settings
from .provider_http_fixture import ProviderHTTPFixture
from . import test_router_credentials as credential_tests


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1", NEXUS_PROVIDER_MODEL_INFO_ENABLED=False)
class PersonalModelInfoTests(ProviderHTTPFixture, TestCase):
    source = credential_tests.PersonalRouterCredentialTests.source
    post = credential_tests.PersonalRouterCredentialTests.post
    runtime = credential_tests.PersonalRouterCredentialTests.runtime
    setUp = credential_tests.PersonalRouterCredentialTests.setUp
    issue = credential_tests.PersonalRouterCredentialTests.issue
    revoke = credential_tests.PersonalRouterCredentialTests.revoke

    @patch("apps.gateway.models_dev.snapshot", return_value={})
    def test_router_credential_model_info_and_revocation(self, _snapshot):
        model = self.source_row.canonical_model
        model.context_window = 64000
        model.metadata = {"capacity_override": {"max_output_tokens": 16000}}
        model.save()
        issued, client = self.issue()
        response = client.get("/api/v1/openai/v1/model/info")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row["model_name"] for row in response.data["data"]], [self.model])
        self.assertEqual(response.data["data"][0]["model_info"]["context_window"], 64000)
        row = response.data["data"][0]
        self.assertEqual(row["litellm_params"], {"model": "openai/" + self.model, "custom_llm_provider": "openai"})
        self.assertEqual(row["model_info"]["max_input_tokens"], 64000)
        self.assertTrue(row["nexus"]["input_capacity_from_context"])
        self.assertIsNone(row["model_info"]["base_model"])
        self.assertIsNone(row["model_info"]["input_cost_per_token"])
        for path in ("/api/v1/openai/model/info", "/api/v1/openai/model/info/", "/api/v1/openai/v1/model/info/"):
            result = client.get(path, {"litellm_model_id": row["model_info"]["id"], "include_team_models": "true"})
            self.assertEqual(result.status_code, 200, result.data)
            self.assertEqual(result.data, response.data)
        for params in ({"litellm_model_id": "missing"}, {"healthy_only": "invalid"}, {"teamId": "another-team"}):
            error = client.get("/api/v1/openai/v1/model/info", params)
            self.assertEqual(error.status_code, 400)
            self.assertEqual(set(error.data["error"]), {"message", "type", "code", "param"})
            self.assertEqual(error["Cache-Control"], "private, no-store")
        self.assertIn(client.get("/api/v1/openai/v1/model/info?router_id=00000000-0000-4000-8000-000000000001").status_code, (403, 404))
        self.assertNotIn(issued["gateway_api_key"], str(response.data))
        self.revoke(issued["gateway_api_key_id"])
        self.assertIn(client.get("/api/v1/openai/v1/model/info").status_code, (401, 403))
