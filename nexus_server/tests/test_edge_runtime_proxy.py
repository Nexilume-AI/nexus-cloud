from __future__ import annotations

import base64
import json
import socket
import ssl
import tempfile
import threading
import time
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.agents.models import Agent, AgentRuntimeDeployment, EdgeAgentRegistration, EdgeNode
from apps.agents.runtime_runner import (
    OpenWrtIPv6RuntimeRunner,
    _PinnedIPv6HTTPSConnection,
    _allowed_mcp_headers,
)
from apps.agents.runtime_views import safe_mcp_response_headers
from apps.tenancy.models import Tenant


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _certificate(
    *,
    ca_key,
    ca_certificate,
    common_name: str,
    public_key,
    server: bool,
):
    now = timezone.now()
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .issuer_name(ca_certificate.subject)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.ExtendedKeyUsage(
                [ExtendedKeyUsageOID.SERVER_AUTH if server else ExtendedKeyUsageOID.CLIENT_AUTH]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(public_key), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
    )
    if server:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(common_name)]), critical=False
        )
    return builder.sign(ca_key, hashes.SHA256())


class _IPv6HTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6


class PinnedIPv6HTTPSConnectionTests(SimpleTestCase):
    @patch("apps.agents.runtime_runner.socket.create_connection")
    def test_binds_configured_source_address(self, create_connection) -> None:
        raw_socket = MagicMock()
        wrapped_socket = MagicMock()
        create_connection.return_value = raw_socket
        context = MagicMock()
        context.wrap_socket.return_value = wrapped_socket
        connection = _PinnedIPv6HTTPSConnection(
            address="2001:db8::20",
            port=7443,
            server_hostname="edge.example.test",
            context=context,
            timeout=5,
            source_address="2001:db8::10",
        )

        connection.connect()

        create_connection.assert_called_once_with(
            ("2001:db8::20", 7443),
            5,
            source_address=("2001:db8::10", 0),
        )
        context.wrap_socket.assert_called_once_with(
            raw_socket,
            server_hostname="edge.example.test",
        )
        self.assertIs(connection.sock, wrapped_socket)

    @patch("apps.agents.runtime_runner.socket.create_connection")
    def test_uses_normal_source_selection_when_unconfigured(self, create_connection) -> None:
        raw_socket = MagicMock()
        create_connection.return_value = raw_socket
        context = MagicMock()
        connection = _PinnedIPv6HTTPSConnection(
            address="2001:db8::20",
            port=7443,
            server_hostname="edge.example.test",
            context=context,
            timeout=5,
        )

        connection.connect()

        create_connection.assert_called_once_with(("2001:db8::20", 7443), 5)


class EdgeRuntimeTLSProxyTests(TestCase):
    origin = "agent://edge-test/probe-1"
    mcp_path = "/mcp/agent%3A%2F%2Fedge-test%2Fprobe-1"
    tls_name = "router-edge.test"

    def test_mtls_edge_jwt_sni_and_full_mcp_handshake(self) -> None:
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = timezone.now()
        ca_certificate = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Nexus Edge E2E CA")]))
            .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Nexus Edge E2E CA")]))
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=2))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=None,
                    decipher_only=None,
                ),
                critical=True,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False
            )
            .sign(ca_key, hashes.SHA256())
        )
        server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        client_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        edge_signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        server_certificate = _certificate(
            ca_key=ca_key,
            ca_certificate=ca_certificate,
            common_name=self.tls_name,
            public_key=server_key.public_key(),
            server=True,
        )
        client_certificate = _certificate(
            ca_key=ca_key,
            ca_certificate=ca_certificate,
            common_name="nexus-server-edge-client",
            public_key=client_key.public_key(),
            server=False,
        )

        records: list[dict] = []
        sni_names: list[str] = []
        handler = self._handler(records=records, edge_public_key=edge_signing_key.public_key())
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            ca_file = temporary / "edge-ca.pem"
            server_cert_file = temporary / "server.pem"
            server_key_file = temporary / "server-key.pem"
            client_cert_file = temporary / "client.pem"
            client_key_file = temporary / "client-key.pem"
            edge_key_file = temporary / "edge-jwt-key.pem"
            ca_file.write_bytes(ca_certificate.public_bytes(serialization.Encoding.PEM))
            server_cert_file.write_bytes(server_certificate.public_bytes(serialization.Encoding.PEM))
            server_key_file.write_bytes(
                server_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            client_cert_file.write_bytes(client_certificate.public_bytes(serialization.Encoding.PEM))
            client_key_file.write_bytes(
                client_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            edge_key_file.write_bytes(
                edge_signing_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )

            tls_server = _IPv6HTTPServer(("::1", 0), handler)
            server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_context.minimum_version = ssl.TLSVersion.TLSv1_2
            server_context.load_cert_chain(str(server_cert_file), str(server_key_file))
            server_context.load_verify_locations(cafile=str(ca_file))
            server_context.verify_mode = ssl.CERT_REQUIRED
            server_context.set_servername_callback(
                lambda _socket, server_name, _context: sni_names.append(server_name or "")
            )
            tls_server.socket = server_context.wrap_socket(tls_server.socket, server_side=True)
            thread = threading.Thread(target=tls_server.serve_forever, daemon=True)
            thread.start()
            try:
                deployment = self._deployment(port=tls_server.server_port)
                with override_settings(
                    NEXUS_EDGE_CA_BUNDLES={"edge-e2e-ca": str(ca_file)},
                    NEXUS_EDGE_CLIENT_CERT_FILE=str(client_cert_file),
                    NEXUS_EDGE_CLIENT_KEY_FILE=str(client_key_file),
                    NEXUS_EDGE_ALLOW_WITHOUT_MTLS=False,
                    NEXUS_EDGE_JWT_PRIVATE_KEY_FILE=str(edge_key_file),
                    NEXUS_EDGE_JWT_KEY_ID="edge-e2e-1",
                    NEXUS_EDGE_JWT_ISSUER="https://nexus.test/edge",
                    NEXUS_EDGE_JWT_TTL_SECONDS=60,
                    NEXUS_EDGE_REQUEST_TIMEOUT_SECONDS=5,
                ):
                    runner = OpenWrtIPv6RuntimeRunner()
                    initialized = self._call(
                        runner,
                        deployment,
                        {"jsonrpc": "2.0", "id": "init-1", "method": "initialize", "params": {}},
                    )
                    notification = self._raw_call(
                        runner,
                        deployment,
                        {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    )
                    listed = self._call(
                        runner,
                        deployment,
                        {"jsonrpc": "2.0", "id": "list-1", "method": "tools/list", "params": {}},
                    )
                    nonce = "nonce-edge-runtime-42"
                    called = self._call(
                        runner,
                        deployment,
                        {
                            "jsonrpc": "2.0",
                            "id": "call-1",
                            "method": "tools/call",
                            "params": {"name": "edge_probe", "arguments": {"nonce": nonce}},
                        },
                    )
            finally:
                tls_server.shutdown()
                tls_server.server_close()
                thread.join(timeout=5)

        self.assertEqual(initialized["id"], "init-1")
        self.assertEqual(initialized["result"]["serverInfo"]["name"], "nexus-openwrt-agent-router")
        self.assertEqual(notification.status_code, 202)
        self.assertEqual([tool["name"] for tool in listed["result"]["tools"]], ["edge_probe"])
        self.assertEqual(called["id"], "call-1")
        self.assertEqual(called["result"]["structuredContent"]["nonce"], nonce)
        self.assertEqual(called["result"]["structuredContent"]["task_id"], "mcp:call-1")
        self.assertEqual(len(records), 4)
        self.assertTrue(all(record["path"] == self.mcp_path for record in records))
        self.assertTrue(all(record["target"] == self.origin for record in records))
        self.assertTrue(all(record["client_certificate"] for record in records))
        self.assertTrue(all(record["deadline"] for record in records))
        self.assertEqual(sni_names, [self.tls_name] * 4)

    def _handler(self, *, records: list[dict], edge_public_key):
        origin = self.origin
        mcp_path = self.mcp_path

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length))
                authorization = self.headers.get("Authorization", "")
                assert authorization.startswith("Bearer ")
                token = authorization.removeprefix("Bearer ")
                header, claims, signature = token.split(".")
                edge_public_key.verify(
                    _b64decode(signature),
                    f"{header}.{claims}".encode("ascii"),
                    padding.PKCS1v15(),
                    hashes.SHA256(),
                )
                decoded_claims = json.loads(_b64decode(claims))
                assert decoded_claims["target_agent"] == origin
                assert decoded_claims["exp"] > int(time.time())
                records.append(
                    {
                        "path": self.path,
                        "target": self.headers.get("X-Nexus-Target-Agent"),
                        "deadline": self.headers.get("X-Nexus-Deadline"),
                        "client_certificate": bool(self.connection.getpeercert()),
                        "claims": decoded_claims,
                    }
                )
                assert self.path == mcp_path
                method = payload["method"]
                if method == "notifications/initialized":
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if method == "initialize":
                    result = {
                        "protocolVersion": "2025-03-26",
                        "serverInfo": {"name": "nexus-openwrt-agent-router", "version": "0.6.0"},
                        "capabilities": {"tools": {"listChanged": False}},
                    }
                elif method == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": "edge_probe",
                                "description": "Terminal execution probe",
                                "inputSchema": {
                                    "type": "object",
                                    "required": ["nonce"],
                                    "properties": {"nonce": {"type": "string"}},
                                    "additionalProperties": False,
                                },
                            }
                        ]
                    }
                else:
                    arguments = payload["params"]["arguments"]
                    proof = {
                        "nonce": arguments["nonce"],
                        "task_id": f"mcp:{payload['id']}",
                        "terminal_instance_id": "terminal-fixture-1",
                    }
                    result = {
                        "content": [{"type": "text", "text": json.dumps(proof)}],
                        "structuredContent": proof,
                        "isError": False,
                    }
                raw = json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": result}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, _format, *_args):
                return

        return Handler

    def _deployment(self, *, port: int):
        user = get_user_model().objects.create_user(username="edge_proxy_owner")
        tenant = Tenant.objects.create(name="Edge Proxy Tenant", slug="edge-proxy-tenant")
        agent = Agent.objects.create(tenant=tenant, name="Edge Probe", created_by=user)
        node = EdgeNode.objects.create(
            tenant=tenant,
            router_id="router-edge-e2e",
            domain_id="edge.test",
            display_name="Edge E2E Router",
            device_token_hash="0" * 64,
        )
        registration = EdgeAgentRegistration.objects.create(
            node=node,
            agent=agent,
            origin=self.origin,
            route_id="route-edge-probe",
            protocols=["mcp"],
            capabilities=["nexus.e2e.edge_probe"],
            ipv6_address="::1",
            port=port,
            path=self.mcp_path,
            tls_server_name=self.tls_name,
            ca_bundle_id="edge-e2e-ca",
            lease_expires_at=timezone.now() + timedelta(minutes=5),
            last_renewed_at=timezone.now(),
        )
        return AgentRuntimeDeployment.objects.create(
            tenant=tenant,
            agent=agent,
            runtime_kind=AgentRuntimeDeployment.RUNTIME_OPENWRT_IPV6,
            image=None,
            edge_registration=registration,
            status=AgentRuntimeDeployment.STATUS_ACTIVE,
        )

    def _raw_call(self, runner, deployment, payload):
        return runner.call_mcp(
            deployment=deployment,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            body=json.dumps(payload, separators=(",", ":")).encode(),
        )

    def _call(self, runner, deployment, payload):
        response = self._raw_call(runner, deployment, payload)
        self.assertEqual(response.status_code, 200, response.body)
        return json.loads(response.body)


class MCPResponseHeaderTests(SimpleTestCase):
    def test_only_mcp_and_nexus_injected_request_headers_cross_runtime_boundary(self) -> None:
        result = _allowed_mcp_headers(
            headers={
                "Content-Type": "application/json",
                "MCP-Protocol-Version": "2025-06-18",
                "Last-Event-ID": "event-9",
                "X-Nexus-AGUI-Run-Id": "run-1",
                "X-Nexus-Workspace-Delegate-Token": "delegate-token",
                "Authorization": "caller-secret",
                "Cookie": "caller-cookie",
                "X-Nexus-End-User": "raw-caller",
            },
            body=b"{}",
        )
        self.assertEqual(result["X-Nexus-AGUI-Run-Id"], "run-1")
        self.assertEqual(result["X-Nexus-Workspace-Delegate-Token"], "delegate-token")
        self.assertEqual(result["Last-Event-ID"], "event-9")
        self.assertNotIn("Authorization", result)
        self.assertNotIn("Cookie", result)
        self.assertNotIn("X-Nexus-End-User", result)

    def test_hop_by_hop_and_connection_named_headers_are_removed(self) -> None:
        result = safe_mcp_response_headers(
            {
                "Content-Type": "application/json",
                "Mcp-Session-Id": "session-1",
                "Connection": "close, X-Internal-Hop",
                "X-Internal-Hop": "remove-me",
                "Transfer-Encoding": "chunked",
                "Set-Cookie": "container-secret=must-not-cross-gateway",
            }
        )

        self.assertEqual(
            result,
            {"Content-Type": "application/json", "Mcp-Session-Id": "session-1"},
        )
