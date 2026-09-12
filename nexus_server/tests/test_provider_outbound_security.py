from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError

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
