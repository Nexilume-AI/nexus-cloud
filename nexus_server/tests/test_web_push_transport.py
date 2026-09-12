"""Real Push encryption with intercepted transport; never contact supplied URLs."""
import base64
from types import SimpleNamespace
from unittest.mock import Mock, patch
import socket

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from django.test import SimpleTestCase, override_settings
from py_vapid import Vapid
from requests import Response
from pywebpush import WebPushException

from apps.gateway.provider_http import ProviderEndpointRejected
from apps.notifications import push


class WebPushTransportTests(SimpleTestCase):
    def setUp(self):
        vapid = Vapid()
        vapid.generate_keys()
        self.policy = override_settings(NEXUS_WEB_PUSH_ENABLED=True,
            NEXUS_VAPID_PUBLIC_KEY="configured", NEXUS_VAPID_PRIVATE_KEY=vapid,
            NEXUS_VAPID_SUBJECT="mailto:operator@example.test", NEXUS_PRODUCTION=False,
            NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="push.example.test,127.0.0.1",
            NEXUS_PROVIDER_ALLOW_HTTP=True)
        self.policy.enable()
        self.addCleanup(self.policy.disable)
        receiver = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint)
        self.keys = {"p256dh": base64.urlsafe_b64encode(receiver).decode().rstrip("="),
                     "auth": base64.urlsafe_b64encode(b"test-auth-secret").decode().rstrip("=")}

    def deliver(self, endpoint):
        info = {"endpoint": endpoint, "keys": self.keys}
        with patch.object(push, "_subscription_info", return_value=info):
            push._deliver(SimpleNamespace(), {"type": "test", "body": "Generic notice"})

    def test_private_endpoint_is_rejected_before_default_requests_can_send(self):
        response = Response()
        response.status_code, response._content = 201, b""
        rows = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        with patch("socket.getaddrinfo", return_value=rows), patch("requests.post", return_value=response) as sender:
            with self.assertRaises(ProviderEndpointRejected):
                self.deliver("https://127.0.0.1/private-token")
            sender.assert_not_called()

    def test_mixed_dns_and_private_ipv6_rejected_even_with_provider_exceptions(self):
        public = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))
        for address in ("127.0.0.1", "10.1.2.3", "169.254.169.254", "::1", "fd00::1", "::ffff:127.0.0.1"):
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            private = (family, socket.SOCK_STREAM, 6, "", (address, 443))
            with self.subTest(address=address), patch("socket.getaddrinfo", return_value=[public, private]), \
                    patch("apps.gateway.provider_http._PinnedHTTPSConnection") as connection, \
                    patch("requests.post") as default_sender:
                with self.assertRaises(ProviderEndpointRejected):
                    self.deliver("https://push.example.test/private-token")
                connection.assert_not_called()
                default_sender.assert_not_called()

    def test_transport_requires_https_authority_independent_of_provider_http_settings(self):
        for url in ("http://push.example.test/x", "https://user:secret@push.example.test/x",
                    "https://push.example.test/x#fragment", "https://push.example.test:0/x",
                    "https://push.example.test/a b"):
            with self.subTest(url=url), patch("requests.post") as sender:
                with self.assertRaises(ProviderEndpointRejected):
                    push._PublicPushSession().post(url, data=b"encrypted", headers={}, timeout=5)
                sender.assert_not_called()

    def connection(self, status=201):
        response = SimpleNamespace(status=status, reason="Untrusted reason", headers={"Location": "https://127.0.0.1/private"},
                                   read=Mock(return_value=b"private-body-must-not-leak"))
        return Mock(getresponse=Mock(return_value=response)), response

    def test_real_encryption_uses_one_pinned_resolution_and_no_default_proxy_transport(self):
        connection, response = self.connection()
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
        rebound = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        with patch("socket.getaddrinfo", side_effect=[public, rebound]) as dns, \
                patch("apps.gateway.provider_http._PinnedHTTPSConnection", return_value=connection) as factory, \
                patch("requests.post") as sender:
            self.deliver("https://push.example.test/send?opaque=subscription")
        dns.assert_called_once()
        endpoint = factory.call_args.args[0]
        self.assertEqual(endpoint.hostname, "push.example.test")
        self.assertEqual(endpoint.addresses[0][-1], ("8.8.8.8", 443))
        self.assertEqual(connection.request.call_args.args, ("POST", "/send?opaque=subscription"))
        request = connection.request.call_args.kwargs
        self.assertIsInstance(request["body"], bytes)
        self.assertNotIn(b"Generic notice", request["body"])
        self.assertTrue(request["headers"]["Authorization"].startswith("vapid "))
        sender.assert_not_called()
        response.read.assert_not_called()
        connection.close.assert_called_once()

    def test_redirects_and_error_bodies_never_escape_and_delivery_status_is_preserved(self):
        for status in (301, 307, 404, 410, 429, 500):
            connection, response = self.connection(status)
            rows = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
            with self.subTest(status=status), patch("socket.getaddrinfo", return_value=rows) as dns, \
                    patch("apps.gateway.provider_http._PinnedHTTPSConnection", return_value=connection), \
                    patch("requests.post") as sender:
                with self.assertRaises(WebPushException) as raised:
                    self.deliver("https://push.example.test/private-token")
                self.assertEqual(raised.exception.response.status_code, status)
                self.assertNotIn("private-body", str(raised.exception))
                self.assertNotIn("private-token", str(raised.exception))
                dns.assert_called_once()
                response.read.assert_called_once_with(1)
                connection.request.assert_called_once()
                connection.close.assert_called_once()
                sender.assert_not_called()
