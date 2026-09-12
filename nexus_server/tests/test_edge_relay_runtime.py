from __future__ import annotations

import base64
import hashlib
import json
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.agents.edge_services import record_presence_sweeper_heartbeat, secret_hash
from apps.agents.edge_serializers import EdgeAgentRegistrationUpsertSerializer
from apps.agents.models import Agent, AgentDisplayRun, AgentRuntimeDeployment, EdgeAgentRegistration, EdgeNode
from apps.agents.relay_services import forwarding_trust_descriptor, issue_forwarding_assertion
from apps.agents.runtime_runner import OpenWrtRelayRuntimeRunner
from apps.agents.runtime_services import OpenWrtIPv6Required, ensure_openwrt_interactive_transport
from apps.tenancy.models import Tenant


RELAY_CONFIG = {
    "relay-a": {
        "router_id": "relay-router-a",
        "domain_id": "relay.test",
        "assignment_endpoint": "https://relay.test:7444/arpx/v1",
        "invoke_endpoint": "https://relay.test:7445/cloud/invoke/v1",
        "connect_ipv4": "192.0.2.44",
    }
}
TICKET_SECRET = base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode().rstrip("=")


class EdgeRegistrationMediaContractTests(SimpleTestCase):
    def _relay_registration(self, tool: dict) -> dict:
        return {
            "origin": "agent://relay/media",
            "route_id": "route-media",
            "protocols": ["mcp"],
            "capabilities": ["nexus.media.inspect"],
            "transport": "relay",
            "relay_id": "relay-a",
            "relay_router_id": "router-nat-a",
            "relay_assignment_id": "assignment-a",
            "generation": 1,
            "mcp_tools": [{
                "name": "inspect_media",
                "intent": "nexus.media.inspect",
                **tool,
            }],
        }

    def test_image_modality_requires_attachment_array(self) -> None:
        serializer = EdgeAgentRegistrationUpsertSerializer(data=self._relay_registration({
            "input_modalities": ["text", "image"],
            "input_schema": {
                "type": "object",
                "properties": {"content": {"type": "string"}},
            },
        }))

        self.assertFalse(serializer.is_valid())
        self.assertIn("attachments array", str(serializer.errors))

    def test_image_modality_with_attachment_array_is_preserved(self) -> None:
        serializer = EdgeAgentRegistrationUpsertSerializer(data=self._relay_registration({
            "input_modalities": ["text", "image"],
            "input_schema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "attachments": {"type": "array", "items": {"type": "object"}},
                },
            },
        }))

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(
            serializer.validated_data["mcp_tools"][0]["input_modalities"],
            ["text", "image"],
        )


class RelayDirectoryTests(TestCase):
    def test_mcp_tool_intent_must_be_a_published_capability(self) -> None:
        serializer = EdgeAgentRegistrationUpsertSerializer(data={
            "origin": "agent://relay/terminal",
            "route_id": "route-terminal",
            "protocols": ["mcp"],
            "capabilities": ["nexus.safe.intent"],
            "transport": "relay",
            "relay_id": "relay-a",
            "relay_router_id": "router-nat-a",
            "relay_assignment_id": "assignment-a",
            "generation": 1,
            "mcp_tools": [{
                "name": "unsafe_tool",
                "intent": "nexus.unpublished.intent",
                "input_schema": {"type": "object"},
            }],
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn("published capabilities", str(serializer.errors))

    @override_settings(
        NEXUS_EDGE_REQUIRE_MTLS_HEADER=False,
        NEXUS_RELAY_ENDPOINTS=RELAY_CONFIG,
        NEXUS_RELAY_TICKET_KEYS={"relay-ticket-1": TICKET_SECRET},
        NEXUS_RELAY_TICKET_ACTIVE_KEY_ID="relay-ticket-1",
    )
    def test_enrolled_device_receives_bound_relay_assignment(self) -> None:
        tenant = Tenant.objects.create(name="Relay Tenant", slug="relay-tenant")
        token = "edge_relay_device_token"
        node = EdgeNode.objects.create(
            tenant=tenant,
            router_id="router-nat-a",
            domain_id="edge.test",
            display_name="NAT Router",
            device_token_hash=secret_hash(token),
            connectivity_mode=EdgeNode.CONNECTIVITY_RELAY,
        )
        client = APIClient()
        with patch("apps.agents.relay_services.require_relay_configuration"):
            response = client.post(
                "/api/v1/edge/v1/relay-assignment/",
                {
                    "version": 1,
                    "router_id": "router-nat-a",
                    "domain_id": "edge.test",
                    "current_relay_id": "",
                    "failed_relay_id": "",
                },
                format="json",
                HTTP_AUTHORIZATION=f"Edge {token}",
                HTTP_ACCEPT="application/vnd.nexus.relay-assignment+json",
            )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.nexus.relay-assignment+json",
        )
        data = response.json()
        self.assertEqual(data["relay_id"], "relay-a")
        self.assertEqual(data["relay_router_id"], "relay-router-a")
        self.assertTrue(data["session_ticket"].startswith("nrt1.relay-ticket-1."))
        node.refresh_from_db()
        self.assertEqual(node.relay_id, "relay-a")
        self.assertGreater(node.relay_lease_expires_at, timezone.now())

    @override_settings(
        NEXUS_EDGE_REQUIRE_MTLS_HEADER=False,
        NEXUS_RELAY_ENDPOINTS=RELAY_CONFIG,
        NEXUS_RELAY_TICKET_KEYS={"relay-ticket-1": TICKET_SECRET},
        NEXUS_RELAY_TICKET_ACTIVE_KEY_ID="relay-ticket-1",
    )
    def test_single_trusted_relay_can_be_reassigned_after_failure(self) -> None:
        tenant = Tenant.objects.create(name="Relay Recovery", slug="relay-recovery")
        token = "edge_relay_recovery_token"
        EdgeNode.objects.create(
            tenant=tenant,
            router_id="router-recovery-a",
            domain_id="edge.test",
            display_name="Recovering NAT Router",
            device_token_hash=secret_hash(token),
            connectivity_mode=EdgeNode.CONNECTIVITY_RELAY,
            relay_id="relay-a",
        )
        with patch("apps.agents.relay_services.require_relay_configuration"):
            response = APIClient().post(
                "/api/v1/edge/v1/relay-assignment/",
                {
                    "version": 1,
                    "router_id": "router-recovery-a",
                    "domain_id": "edge.test",
                    "current_relay_id": "relay-a",
                    "failed_relay_id": "relay-a",
                },
                format="json",
                HTTP_AUTHORIZATION=f"Edge {token}",
                HTTP_ACCEPT="application/vnd.nexus.relay-assignment+json",
            )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["relay_id"], "relay-a")
        self.assertTrue(response.json()["session_ticket"].startswith("nrt1.relay-ticket-1."))

    @override_settings(
        NEXUS_EDGE_REQUIRE_MTLS_HEADER=False,
        NEXUS_RELAY_ENDPOINTS=RELAY_CONFIG,
    )
    def test_enrolled_device_can_refresh_relay_configuration(self) -> None:
        tenant = Tenant.objects.create(name="Relay Refresh", slug="relay-refresh")
        token = "edge_relay_refresh_token"
        EdgeNode.objects.create(
            tenant=tenant,
            router_id="router-refresh-a",
            domain_id="edge.test",
            display_name="Refresh Router",
            device_token_hash=secret_hash(token),
        )
        client = APIClient()
        with patch("apps.agents.edge_views.relay_configuration_available", return_value=True), patch("apps.agents.edge_views.forwarding_trust_descriptor", return_value={
            "public_key": "PUBLIC", "key_id": "nexus-cloud-1",
            "issuer": "nexus-cloud-e2e", "source_router_id": "nexus-cloud",
        }), patch("apps.agents.edge_views.relay_device_trust_descriptor", return_value={
            "relay_id": "relay-a", "ca_certificate": "CA",
        }):
            response = client.get(
                "/api/v1/edge/v1/relay-configuration/",
                HTTP_AUTHORIZATION=f"Edge {token}",
            )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["router_id"], "router-refresh-a")
        self.assertEqual(data["relay_directory"]["endpoint"], "/api/v1/edge/v1/relay-assignment/")
        self.assertEqual(data["relay_directory"]["forwarding"]["key_id"], "nexus-cloud-1")
        self.assertEqual(data["relay_directory"]["forwarding"]["issuer"], "nexus-cloud-e2e")
        self.assertEqual(data["relay_directory"]["forwarding"]["source_router_id"], "nexus-cloud")

        denied = client.get("/api/v1/edge/v1/relay-configuration/", HTTP_AUTHORIZATION="Edge invalid")
        self.assertIn(denied.status_code, {401, 403})

    @override_settings(
        NEXUS_EDGE_REQUIRE_MTLS_HEADER=False,
        NEXUS_RELAY_ENDPOINTS={},
        NEXUS_RELAY_TICKET_KEYS={},
        NEXUS_RELAY_FORWARDING_PRIVATE_KEY_FILE="",
    )
    def test_authenticated_device_gets_clear_error_when_cloud_relay_is_unavailable(self) -> None:
        tenant = Tenant.objects.create(name="No Relay", slug="no-relay")
        token = "edge_no_relay_token"
        EdgeNode.objects.create(
            tenant=tenant,
            router_id="router-no-relay",
            domain_id="edge.test",
            display_name="No Relay Router",
            device_token_hash=secret_hash(token),
        )

        response = APIClient().get(
            "/api/v1/edge/v1/relay-configuration/",
            HTTP_AUTHORIZATION=f"Edge {token}",
        )

        self.assertEqual(response.status_code, 503, response.content)
        self.assertEqual(response.json()["error"]["code"], "RELAY_NOT_CONFIGURED")


class RelayServiceControlTests(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="relay-viewer")
        self.superuser = user_model.objects.create_superuser(
            username="relay-superuser",
            email="relay-admin@example.test",
            password="unused",
        )

    @staticmethod
    def runtime_status() -> dict:
        return {
            "relay_id": "relay-local",
            "running": True,
            "router_listener_healthy": True,
            "cloud_listener_healthy": True,
            "router_endpoint": {
                "scheme": "https", "host": "192.0.2.44", "port": 27444,
                "path": "/arpx/v1", "scope": "router",
            },
            "cloud_invoke_endpoint": {
                "scheme": "https", "host": "127.0.0.1", "port": 27445,
                "path": "/cloud/invoke/v1", "scope": "cloud_internal",
            },
        }

    def test_authenticated_user_can_discover_safe_ports_but_cannot_manage_relay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, override_settings(
            NEXUS_RELAY_CONTROL_FILE=str(Path(temporary) / "control.json"),
            NEXUS_EDGE_PRESENCE_STATUS_FILE=str(Path(temporary) / "presence-sweeper.json"),
        ), patch("apps.agents.relay_services._validate_static_relay_configuration"), patch(
            "apps.agents.relay_services.relay_runtime_status", side_effect=self.runtime_status
        ):
            record_presence_sweeper_heartbeat(now=timezone.now(), expired_count=2)
            client = APIClient()
            client.force_authenticate(user=self.user)
            response = client.get("/api/v1/edge/relay-service/")
            denied = client.post("/api/v1/edge/relay-service/", {"enabled": True}, format="json")

        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertFalse(data["can_manage"])
        self.assertEqual(data["router_endpoint"]["port"], 27444)
        self.assertEqual(data["cloud_invoke_endpoint"]["scope"], "cloud_internal")
        self.assertTrue(data["presence_sweeper"]["automatic"])
        self.assertTrue(data["presence_sweeper"]["running"])
        self.assertEqual(data["presence_sweeper"]["expired_last_run"], 2)
        self.assertEqual(denied.status_code, 403, denied.content)

    def test_superuser_can_enable_and_disable_healthy_relay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, override_settings(
            NEXUS_RELAY_CONTROL_FILE=str(Path(temporary) / "control.json"),
        ), patch("apps.agents.relay_services._validate_static_relay_configuration"), patch(
            "apps.agents.relay_services.relay_runtime_status", side_effect=self.runtime_status
        ):
            client = APIClient()
            client.force_authenticate(user=self.superuser)
            enabled = client.post("/api/v1/edge/relay-service/", {"enabled": True}, format="json")
            disabled = client.post("/api/v1/edge/relay-service/", {"enabled": False}, format="json")

        self.assertEqual(enabled.status_code, 200, enabled.content)
        self.assertTrue(enabled.json()["data"]["available"])
        self.assertTrue(enabled.json()["data"]["can_manage"])
        self.assertEqual(disabled.status_code, 200, disabled.content)
        self.assertFalse(disabled.json()["data"]["enabled"])

    def test_superuser_cannot_enable_unhealthy_relay(self) -> None:
        unhealthy = {**self.runtime_status(), "running": False, "router_listener_healthy": False}
        with tempfile.TemporaryDirectory() as temporary, override_settings(
            NEXUS_RELAY_CONTROL_FILE=str(Path(temporary) / "control.json"),
        ), patch("apps.agents.relay_services._validate_static_relay_configuration"), patch(
            "apps.agents.relay_services.relay_runtime_status", return_value=unhealthy
        ):
            client = APIClient()
            client.force_authenticate(user=self.superuser)
            response = client.post("/api/v1/edge/relay-service/", {"enabled": True}, format="json")

        self.assertEqual(response.status_code, 400, response.content)


class RelayRuntimeTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="relay-runtime")
        self.tenant = Tenant.objects.create(name="Runtime Relay", slug="runtime-relay")
        self.agent = Agent.objects.create(tenant=self.tenant, name="Relay Edge Probe", created_by=self.user)
        now = timezone.now()
        self.node = EdgeNode.objects.create(
            tenant=self.tenant,
            router_id="router-nat-a",
            domain_id="edge.test",
            display_name="NAT Router",
            device_token_hash="1" * 64,
            connectivity_mode=EdgeNode.CONNECTIVITY_RELAY,
            relay_id="relay-a",
            relay_assignment_id="assignment-a",
            relay_lease_expires_at=now + timedelta(minutes=5),
        )
        self.registration = EdgeAgentRegistration.objects.create(
            node=self.node,
            agent=self.agent,
            origin="agent://runtime-relay/edge-probe",
            route_id="route-edge-probe",
            protocols=["mcp"],
            capabilities=["nexus.e2e.edge_probe"],
            mcp_tools=[{
                "name": "edge_probe",
                "title": "Edge probe",
                "description": "Prove terminal execution",
                "input_schema": {"type": "object", "properties": {"nonce": {"type": "string"}}, "required": ["nonce"]},
                "intent": "nexus.e2e.edge_probe",
                "intent_version": 1,
            }],
            transport=EdgeAgentRegistration.TRANSPORT_RELAY,
            relay_id="relay-a",
            relay_router_id="router-nat-a",
            relay_assignment_id="assignment-a",
            generation=1,
            lease_expires_at=now + timedelta(minutes=5),
            last_renewed_at=now,
        )
        self.runtime = AgentRuntimeDeployment.objects.create(
            tenant=self.tenant,
            agent=self.agent,
            runtime_kind=AgentRuntimeDeployment.RUNTIME_OPENWRT_RELAY,
            edge_registration=self.registration,
            status=AgentRuntimeDeployment.STATUS_ACTIVE,
        )
        self.runner = OpenWrtRelayRuntimeRunner()

    def test_interactive_tool_requires_ipv6_before_creating_a_run(self) -> None:
        before = AgentDisplayRun.objects.count()
        with self.assertRaises(OpenWrtIPv6Required) as captured:
            ensure_openwrt_interactive_transport(
                runtime=self.runtime,
                policy={"task": True, "chat": True, "interactive": True},
            )
        self.assertEqual(captured.exception.default_code, "OPENWRT_IPV6_REQUIRED")
        self.assertEqual(AgentDisplayRun.objects.count(), before)

    def call(self, payload):
        return self.runner.call_mcp(
            deployment=self.runtime,
            method="POST",
            headers={"MCP-Protocol-Version": "2025-06-18"},
            body=json.dumps(payload, separators=(",", ":")).encode(),
        )

    def test_control_plane_and_tools_call_envelope(self) -> None:
        initialized = self.call({"jsonrpc": "2.0", "id": "init", "method": "initialize", "params": {}})
        listed = self.call({"jsonrpc": "2.0", "id": "list", "method": "tools/list", "params": {}})
        self.assertEqual(json.loads(initialized.body)["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(json.loads(listed.body)["result"]["tools"][0]["name"], "edge_probe")

        def fake_relay_invoke(**kwargs):
            envelope = json.loads(kwargs["body"])
            self.assertEqual(envelope["task_id"], "mcp:call-1")
            self.assertEqual(envelope["payload"]["request"]["params"]["arguments"]["nonce"], "nonce-1")
            self.assertEqual(kwargs["target_router_id"], "router-nat-a")
            return 200, {"Content-Type": "application/json"}, json.dumps({
                "nonce": "nonce-1", "task_id": envelope["task_id"], "terminal_instance": "terminal-1"
            }).encode()

        with patch("apps.agents.relay_services.relay_invoke", side_effect=fake_relay_invoke):
            result = self.call({
                "jsonrpc": "2.0",
                "id": "call-1",
                "method": "tools/call",
                "params": {"name": "edge_probe", "arguments": {"nonce": "nonce-1"}},
            })
        body = json.loads(result.body)
        self.assertEqual(body["id"], "call-1")
        self.assertEqual(body["result"]["structuredContent"]["terminal_instance"], "terminal-1")

    def test_tools_call_carries_single_use_run_context_exchange(self) -> None:
        def fake_relay_invoke(**kwargs):
            envelope = json.loads(kwargs["body"])
            self.assertEqual(
                envelope["nexus_run_context"],
                {
                    "exchange_url": "https://cloud.example/api/v1/internal/agent-run-contexts/grant/exchange/",
                    "exchange_token": "one-time-token",
                },
            )
            serialized = kwargs["body"].decode()
            self.assertNotIn("full-agui-secret", serialized)
            self.assertNotIn("full-billing-secret", serialized)
            return 200, {"Content-Type": "application/json"}, b'{"ok":true}'

        headers = {
            "MCP-Protocol-Version": "2025-06-18",
            "X-Nexus-AGUI-Run-Id": "run-1",
            "X-Nexus-AGUI-Token": "full-agui-secret",
            "X-Nexus-Billing-Token": "full-billing-secret",
        }
        with (
            patch(
                "apps.agents.runtime_context.prepare_openwrt_run_context_headers",
                return_value={
                    "MCP-Protocol-Version": "2025-06-18",
                    "X-Nexus-Run-Context-Url": "https://cloud.example/api/v1/internal/agent-run-contexts/grant/exchange/",
                    "X-Nexus-Run-Context-Token": "one-time-token",
                },
            ) as prepare_context,
            patch("apps.agents.relay_services.relay_invoke", side_effect=fake_relay_invoke),
        ):
            result = self.runner.call_mcp(
                deployment=self.runtime,
                method="POST",
                headers=headers,
                body=json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "call-context",
                        "method": "tools/call",
                        "params": {"name": "edge_probe", "arguments": {"nonce": "nonce-context"}},
                    },
                    separators=(",", ":"),
                ).encode(),
            )

        self.assertEqual(result.status_code, 200)
        prepare_context.assert_called_once_with(deployment=self.runtime, headers=headers)

    def test_forwarding_assertion_matches_nfa1_signature_contract(self) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with tempfile.TemporaryDirectory() as temporary:
            filename = Path(temporary) / "nfa.pem"
            filename.write_bytes(private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ))
            body = b'{"task_id":"mcp:nfa-1"}'
            with override_settings(
                NEXUS_RELAY_FORWARDING_PRIVATE_KEY_FILE=str(filename),
                NEXUS_RELAY_FORWARDING_KEY_ID="nexus-cloud-1",
                NEXUS_RELAY_FORWARDING_ISSUER="nexus-cloud-e2e",
                NEXUS_RELAY_SOURCE_ROUTER_ID="nexus-cloud",
            ):
                descriptor = forwarding_trust_descriptor()
                assertion = issue_forwarding_assertion(
                    source_router_id="nexus-cloud",
                    target_router_id="router-nat-a",
                    source_agent="service://nexus-server",
                    tenant="tenant-a",
                    intent="nexus.e2e.edge_probe",
                    task_id="mcp:nfa-1",
                    hop_limit=8,
                    body=body,
                )
        self.assertEqual(descriptor["issuer"], "nexus-cloud-e2e")
        self.assertEqual(descriptor["source_router_id"], "nexus-cloud")
        prefix, kid, encoded, signature = assertion.split(".")
        self.assertEqual((prefix, kid), ("nfa1", "nexus-cloud-1"))
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        self.assertEqual(payload[0], 1)
        self.assertEqual(payload[1], 8)
        self.assertEqual(payload[36:68], hashlib.sha256(body).digest())
        offset = 68
        fields = []
        for _ in range(7):
            length = int.from_bytes(payload[offset:offset + 2], "big")
            offset += 2
            fields.append(payload[offset:offset + length].decode("ascii"))
            offset += length
        self.assertEqual(fields[0], "nexus-cloud-e2e")
        self.assertEqual(fields[1], "nexus-cloud")
        private_key.public_key().verify(
            base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)),
            f"nfa1.{kid}.{encoded}".encode("ascii"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
