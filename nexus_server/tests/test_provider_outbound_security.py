from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from django.test import SimpleTestCase, override_settings

from apps.gateway.provider_http import (
    ProviderEndpointRejected,
    open_provider_url,
    resolve_provider_endpoint,
    validate_provider_base_url,
)
from apps.gateway.provider_adapters import managed_provider_endpoint


class _RedirectFixture(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(302)
        self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
        self.end_headers()


class ProviderOutboundSecurityTests(SimpleTestCase):
    def test_direct_api_runtime_cannot_claim_managed_private_endpoint_exemption(self):
        runtime = type("Runtime", (), {"runtime_type": "direct_api", "internal_api_url": "http://127.0.0.1:8317/v1"})()
        deployment = type("Deployment", (), {"provider_runtime": runtime})()

        self.assertFalse(managed_provider_endpoint(deployment=deployment, url=runtime.internal_api_url))

    def test_provider_base_url_requires_https_by_default(self):
        with self.assertRaisesRegex(ProviderEndpointRejected, "HTTPS"):
            validate_provider_base_url("http://provider.example/v1")

    @patch("apps.gateway.provider_http.socket.getaddrinfo")
    def test_endpoint_rejects_loopback_and_link_local_resolution(self, getaddrinfo):
        for address in ("127.0.0.1", "169.254.169.254", "::1"):
            with self.subTest(address=address):
                family = 10 if ":" in address else 2
                getaddrinfo.return_value = [(family, 1, 6, "", (address, 443, 0, 0) if family == 10 else (address, 443))]
                with self.assertRaisesRegex(ProviderEndpointRejected, "public addresses"):
                    resolve_provider_endpoint("https://provider.example/v1/models")

    @override_settings(NEXUS_PROVIDER_ALLOW_HTTP=True, NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1")
    def test_redirect_is_returned_as_error_and_never_followed(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectFixture)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with self.assertRaises(HTTPError) as captured:
            with open_provider_url(
                f"http://127.0.0.1:{server.server_port}/v1/models",
                method="GET",
                timeout=2,
            ):
                pass
        self.assertEqual(captured.exception.code, 302)

    @patch("apps.gateway.provider_http.socket.getaddrinfo")
    @override_settings(NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="provider.internal")
    def test_explicit_private_host_allowlist_is_exact(self, getaddrinfo):
        getaddrinfo.return_value = [(2, 1, 6, "", ("10.0.0.4", 443))]
        endpoint = resolve_provider_endpoint("https://provider.internal/v1")
        self.assertEqual(endpoint.hostname, "provider.internal")
        with self.assertRaises(ProviderEndpointRejected):
            resolve_provider_endpoint("https://sub.provider.internal/v1")


class _UserAgentFixture(BaseHTTPRequestHandler):
    """An upstream that rejects the default Python identity, not its valid key."""

    def log_message(self, _format, *_args):
        return

    def do_GET(self):  # noqa: N802
        if self.headers.get("User-Agent") not in {"Nexus/1.0", "CustomProviderClient/2.0"}:
            self.send_error(403)
            return
        if self.headers.get("Authorization") != "Bearer fixture-key":
            self.send_error(401)
            return
        payload = b'{"data":[{"id":"fixture-model"}]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.do_GET()


@override_settings(NEXUS_PROVIDER_ALLOW_HTTP=True, NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1")
class ProviderUserAgentTests(SimpleTestCase):
    def setUp(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _UserAgentFixture)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base_url = f"http://127.0.0.1:{server.server_port}/v1"
        self.runtime = SimpleNamespace(
            runtime_type="direct_api", internal_api_url=self.base_url,
            source_provider_account=SimpleNamespace(encrypted_key="fixture-encrypted-key"),
        )
        decrypt = patch("apps.providers.runtime_services.decrypt_secret", return_value="fixture-key")
        decrypt.start()
        self.addCleanup(decrypt.stop)

    def test_fixture_rejects_python_default_identity_with_valid_key(self):
        request = Request(self.base_url + "/models", headers={"Authorization": "Bearer fixture-key"})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2)
        self.addCleanup(caught.exception.close)
        self.assertEqual(caught.exception.code, 403)

    def test_direct_catalog_identifies_nexus_and_keeps_authentication(self):
        from apps.providers.runtime_services import discover_runtime_model_ids

        self.assertEqual(discover_runtime_model_ids(runtime=self.runtime), ["fixture-model"])

    def test_direct_model_probe_identifies_nexus_and_keeps_authentication(self):
        from apps.providers.runtime_services import probe_runtime_model

        healthy, _reason = probe_runtime_model(runtime=self.runtime, upstream_model_id="fixture-model")
        self.assertTrue(healthy)

    def test_gateway_identifies_nexus_without_mutating_caller_headers(self):
        headers = {"Authorization": "Bearer fixture-key"}
        with open_provider_url(self.base_url + "/chat/completions", method="POST",
                               headers=headers, body=b"{}", timeout=2) as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(headers, {"Authorization": "Bearer fixture-key"})

    def test_gateway_keeps_explicit_user_agent_case_insensitively(self):
        for name in ("User-Agent", "user-agent"):
            with self.subTest(name=name):
                with open_provider_url(self.base_url + "/models", method="GET", timeout=2,
                                       headers={name: "CustomProviderClient/2.0",
                                                "Authorization": "Bearer fixture-key"}) as response:
                    self.assertEqual(response.status, 200)
