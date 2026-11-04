"""Opt-in real external-client acceptance against Django's live HTTP server."""
import json
import os
from pathlib import Path
import subprocess
from unittest import skipUnless
from unittest.mock import patch
from django.test import LiveServerTestCase, override_settings
from .provider_http_fixture import ProviderHTTPFixture
from . import test_router_credentials as fixtures


@skipUnless(os.environ.get("NEXUS_MODEL_INFO_CLIENT_PYTHON"), "Set the isolated third-party client interpreter")
@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   STATIC_URL="/static/", MEDIA_URL="/media/",
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1", NEXUS_PROVIDER_MODEL_INFO_ENABLED=False)
class ModelInfoClientTests(ProviderHTTPFixture, LiveServerTestCase):
    source = fixtures.PersonalRouterCredentialTests.source
    post = fixtures.PersonalRouterCredentialTests.post
    runtime = fixtures.PersonalRouterCredentialTests.runtime
    issue = fixtures.PersonalRouterCredentialTests.issue

    def setUp(self):
        type(self).setUpTestData()
        fixtures.PersonalRouterCredentialTests.setUp(self)

    @patch("apps.gateway.models_dev.snapshot", return_value={})
    def test_installed_inspect_and_litellm(self, _snapshot):
        model = self.source_row.canonical_model
        model.context_window = 64000
        model.metadata = {"capacity_override": {"max_output_tokens": 16000}}
        model.save()
        issued, _ = self.issue()
        script = Path(__file__).resolve().parents[2] / "tests" / "model_info_third_party_client.py"
        process = subprocess.run([os.environ["NEXUS_MODEL_INFO_CLIENT_PYTHON"], str(script)],
            input=json.dumps({"base_url": self.live_server_url + "/api/v1/openai/v1",
                              "api_key": issued["gateway_api_key"], "model": self.model}),
            text=True, capture_output=True, timeout=90)
        self.assertEqual(process.returncode, 0, process.stderr[-3000:])
        result = json.loads(process.stdout.strip().splitlines()[-1])
        self.assertEqual(result["input_tokens"], 64000)
        self.assertEqual(result["output_tokens"], 16000)
        self.assertTrue(result["client_constructed"])
        print("THIRD_PARTY_ACCEPTANCE", json.dumps(result))
