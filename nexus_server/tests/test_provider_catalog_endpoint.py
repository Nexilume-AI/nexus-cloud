from types import SimpleNamespace
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from django.test import SimpleTestCase, override_settings
from apps.gateway.provider_adapters import OpenAICompatibleAdapter
from tests.test_provider_outbound_security import _UserAgentFixture


@override_settings(NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1",
                   NEXUS_GATEWAY_MOCK_HOSTS=set(), NEXUS_GATEWAY_FAIL_HOSTS=set())
class FullChatEndpointHealthTests(SimpleTestCase):
    def setUp(self):
        self.paths = []
        paths = self.paths
        class Catalog(_UserAgentFixture):
            def do_GET(handler):
                paths.append(handler.path)
                if handler.path != "/v1/models":
                    handler.send_error(404)
                    return
                super().do_GET()
        self.handler = Catalog
        server = ThreadingHTTPServer(("127.0.0.1", 0), Catalog)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base = f"http://127.0.0.1:{server.server_port}/v1"
        self.deployment = SimpleNamespace(endpoint=self.base, model="fixture-model",
            provider_account=SimpleNamespace(url=self.base, encrypted_key="fixture"))
        self.adapter = OpenAICompatibleAdapter()
        decrypt = patch("apps.gateway.provider_adapters.decrypt_secret", return_value="fixture-key")
        decrypt.start()
        self.addCleanup(decrypt.stop)

    def test_full_chat_url_uses_sibling_catalog_without_inference(self):
        for suffix in ("", "/", "/models", "/models/", "/chat/completions", "/chat/completions/"):
            with self.subTest(suffix=suffix), patch.object(self.adapter, "_request_minimal_chat") as chat:
                self.deployment.endpoint = self.base + suffix
                result = self.adapter.check_health(deployment=self.deployment)
                self.assertTrue(result.healthy, result.reason)
                self.assertFalse(result.degraded, result.reason)
                self.assertEqual(self.paths[-1], "/v1/models")
                chat.assert_not_called()

    def test_catalog_auth_failure_does_not_trigger_inference(self):
        self.deployment.endpoint = self.base + "/chat/completions"
        def unauthorized(handler):
            handler.send_error(401)
        with patch.object(self.handler, "do_GET", unauthorized), patch.object(self.adapter, "_request_minimal_chat") as chat:
            result = self.adapter.check_health(deployment=self.deployment)
        self.assertFalse(result.healthy)
        chat.assert_not_called()
