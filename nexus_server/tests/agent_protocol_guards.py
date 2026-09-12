"""Shared MCP error and Edge JWT contracts; no commercial host fixtures."""
import base64
import json
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.agents.edge_auth import edge_jwks, issue_edge_access_token
from apps.agents.models import Agent, AgentRuntimeDeployment, EdgeNode, EdgeAgentRegistration
from apps.agents.runtime_runner import (RuntimeMCPResult, DockerAgentRuntimeRunner,
    RuntimeDisplayContext, RuntimeWorkspaceContext)
from apps.agents.runtime_services import _decode_mcp_task_response, _task_result_failure_details
from apps.tenancy.models import Tenant


class DockerBrowserEnvironmentGuards:
    def test_docker_runner_injects_browser_capture_env_and_browser_tmpfs(self) -> None:
        image = self._register_image()
        image.image_digest = "sha256:" + "a" * 64
        deployment = AgentRuntimeDeployment.objects.create(
            tenant=self.tenant,
            agent=self.agent,
            image=image,
            env="prod",
            status=AgentRuntimeDeployment.STATUS_DEPLOYING,
        )
        display_context = RuntimeDisplayContext(
            run_id="run-1",
            events_url="http://nexus.test/api/v1/internal/ag-ui/runs/run-1/events/",
            write_token="write-token",
            public_url="http://nexus.test/share/agents/agent-1",
        )
        workspace_context = RuntimeWorkspaceContext(
            connection_id="workspace-1",
            root="~/project",
            access_mode=AgentRuntimeDeployment.WORKSPACE_READ_ONLY,
            api_url="http://nexus.test/api/v1/internal/agent-workspaces/runtime-1/",
            token="workspace-token",
        )

        with (
            mock.patch("apps.agents.runtime_runner._run_docker", side_effect=["network-1", "container-1", "0.0.0.0:49153"]) as run_docker,
            mock.patch("apps.agents.runtime_runner._docker_inspect", side_effect=lambda kind, identifier: {"Id": image.image_digest} if kind == "image" else None),
            mock.patch("apps.agents.runtime_runner._wait_for_mcp", return_value=True),
        ):
            result = DockerAgentRuntimeRunner().start(deployment=deployment, display_context=display_context, workspace_context=workspace_context)

        self.assertEqual(result.container_id, "container-1")
        self.assertEqual(result.internal_mcp_url, "http://127.0.0.1:49153/mcp")
        command = next(call.args[0] for call in run_docker.call_args_list if call.args[0][1] == "run")
        self.assertIn("--tmpfs", command)
        self.assertIn("/tmp:rw,nosuid,nodev,size=256m", command)
        self.assertIn("--shm-size", command)
        self.assertIn("256m", command)
        self.assertIn("-p", command)
        binding = command[command.index("-p") + 1]
        self.assertTrue(binding.startswith("127.0.0.1:") and binding.endswith(":8000"))
        self.assertGreater(int(binding.split(":")[1]), 0)
        self.assertNotIn("-P", command)
        self.assertIn("-e", command)
        environment = next(call.kwargs["environment"] for call in run_docker.call_args_list if "environment" in call.kwargs)
        self.assertEqual(environment["NEXUS_AGUI_EVENTS_URL"], display_context.events_url)
        self.assertEqual(environment["NEXUS_AGUI_TOKEN"], "write-token")
        self.assertEqual(environment["NEXUS_BROWSER_CAPTURE_EVENT_NAME"], "nexus.computer.frame")
        self.assertEqual(environment["NEXUS_WORKSPACE_TOKEN"], "workspace-token")
        self.assertEqual(environment["NEXUS_WORKSPACE_ROOT"], "~/project")
        self.assertIn("NEXUS_AGUI_TOKEN", command)
        self.assertNotIn("NEXUS_AGUI_TOKEN=write-token", command)
        self.assertNotIn("NEXUS_WORKSPACE_TOKEN=workspace-token", command)


class MCPTaskResponseGuards:
    def test_task_response_accepts_direct_streamable_http_tool_result(self) -> None:
        payload, succeeded, result_json = _decode_mcp_task_response(
            RuntimeMCPResult(
                status_code=200,
                headers={"Content-Type": "text/event-stream"},
                body=(
                    b"event: message\n"
                    b'data: {"status":"opened","title":"Nexilume AI",'
                    b'"revision":1,"observation_id":"obs-1"}\n\n'
                ),
            )
        )

        self.assertTrue(succeeded)
        self.assertEqual(payload["status"], "opened")
        self.assertEqual(result_json, payload)

    def test_task_response_rejects_direct_streamable_http_tool_error(self) -> None:
        payload, succeeded, result_json = _decode_mcp_task_response(
            RuntimeMCPResult(
                status_code=200,
                headers={"Content-Type": "text/event-stream"},
                body=b'data: {"isError":true,"content":[{"type":"text","text":"failed"}]}\n\n',
            )
        )

        self.assertFalse(succeeded)
        self.assertTrue(payload["isError"])
        self.assertEqual(result_json, {})

    def test_task_response_rejects_named_edge_error_event(self) -> None:
        payload, succeeded, result_json = _decode_mcp_task_response(
            RuntimeMCPResult(
                status_code=200,
                headers={"Content-Type": "text/event-stream"},
                body=(
                    b"event: error\n"
                    b'data: {"code":"MOBILE_BUSY",'
                    b'"message":"Mobile is being controlled by another Run"}\n\n'
                ),
            )
        )

        self.assertFalse(succeeded)
        self.assertEqual(result_json, {})
        self.assertEqual(
            _task_result_failure_details(payload),
            ("MOBILE_BUSY", "Mobile is being controlled by another Run"),
        )

    def test_task_failure_maps_known_mobile_busy_without_exposing_agent_text(self) -> None:
        code, message = _task_result_failure_details(
            {
                "jsonrpc": "2.0",
                "id": "busy",
                "result": {
                    "isError": True,
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Error calling tool 'private-name': "
                                "Mobile is being controlled by another Run"
                            ),
                        }
                    ],
                },
            }
        )
        self.assertEqual(code, "MOBILE_BUSY")
        self.assertEqual(message, "Mobile is busy with another Run.")
        self.assertNotIn("private-name", message)

    def test_task_failure_preserves_mobile_action_status_without_exposing_tool_name(self) -> None:
        cases = (
            (
                "Mobile action was rejected by the caller",
                "MOBILE_ACTION_REJECTED",
                "The Mobile action was declined.",
            ),
            (
                "Mobile action was canceled before completion",
                "MOBILE_ACTION_CANCELLED",
                "The Mobile action was cancelled.",
            ),
            (
                "Mobile action failed on the attached device",
                "MOBILE_ACTION_FAILED",
                "The Mobile action failed on the attached device.",
            ),
            (
                "Mobile action was rejected or failed",
                "MOBILE_ACTION_FAILED",
                "The Mobile action failed on the attached device.",
            ),
        )
        for text, expected_code, expected_message in cases:
            with self.subTest(text=text):
                code, message = _task_result_failure_details(
                    {
                        "result": {
                            "isError": True,
                            "content": [
                                {
                                    "type": "text",
                                    "text": f"Error calling tool 'private-name': {text}",
                                }
                            ],
                        }
                    }
                )
                self.assertEqual(code, expected_code)
                self.assertEqual(message, expected_message)
                self.assertNotIn("private-name", message)


class EdgeJWTGuards:
    def test_edge_token_and_jwks_use_dedicated_rs256_key(self) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with tempfile.TemporaryDirectory() as temporary_directory:
            key_path = Path(temporary_directory) / "edge-private.pem"
            key_path.write_bytes(
                private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )
            with override_settings(
                NEXUS_EDGE_JWT_PRIVATE_KEY_FILE=str(key_path),
                NEXUS_EDGE_JWT_KEY_ID="edge-test-1",
                NEXUS_EDGE_JWT_ISSUER="https://id.example/edge",
            ):
                deployment = self._deployment()
                token = issue_edge_access_token(deployment=deployment)
                header, payload, _signature = token.split(".")
                decoded_header = json.loads(base64.urlsafe_b64decode(header + "=" * (-len(header) % 4)))
                decoded_payload = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
                keys = edge_jwks()["keys"]

        self.assertEqual(decoded_header["alg"], "RS256")
        self.assertEqual(decoded_header["typ"], "at+jwt")
        self.assertEqual(decoded_payload["aud"], "urn:nexus:router:router-jwt")
        self.assertEqual(decoded_payload["target_agent"], "agent://jwt/echo")
        self.assertEqual(decoded_payload["scope"], "agent.route agent.invoke")
        self.assertEqual(keys[0]["kid"], "edge-test-1")

    def test_edge_jwks_endpoint_is_a_raw_jwks_document(self) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with tempfile.TemporaryDirectory() as temporary_directory:
            key_path = Path(temporary_directory) / "edge-private.pem"
            key_path.write_bytes(
                private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )
            with override_settings(NEXUS_EDGE_JWT_PRIVATE_KEY_FILE=str(key_path)):
                response = APIClient().get("/api/v1/edge/.well-known/jwks.json")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("keys", response.json())
        self.assertNotIn("data", response.json())
        self.assertEqual(response["Cache-Control"], "public, max-age=300")

    def _deployment(self):
        user = get_user_model().objects.create_user(username="jwt_owner")
        tenant = Tenant.objects.create(name="JWT Tenant", slug="jwt-tenant")
        agent = Agent.objects.create(tenant=tenant, name="JWTAgent", created_by=user)
        node = EdgeNode.objects.create(
            tenant=tenant,
            router_id="router-jwt",
            domain_id="jwt.example",
            display_name="JWT Router",
            device_token_hash="0" * 64,
        )
        registration = EdgeAgentRegistration.objects.create(
            node=node,
            agent=agent,
            origin="agent://jwt/echo",
            route_id="route-jwt",
            protocols=["mcp"],
            capabilities=["demo.echo"],
            ipv6_address="240e:1234:5678::30",
            port=7443,
            tls_server_name="router-jwt.example",
            ca_bundle_id="edge-ca-1",
            path="/mcp/agent%3A%2F%2Fjwt%2Fecho",
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
