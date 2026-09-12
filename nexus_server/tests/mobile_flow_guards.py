from __future__ import annotations

import json
from datetime import timedelta
from urllib.parse import urlsplit

from django.utils import timezone
from rest_framework.test import APIRequestFactory

from apps.audit.models import AuditLog
from apps.agents.models import (
    Agent,
    AgentDisplayEvent,
    AgentDisplayRun,
    AgentDeployment,
    AgentMobileBinding,
    AgentMobileLease,
    AgentRunInteraction,
    AgentRuntimeDeployment,
    AgentRuntimeImage,
    AgentVersion,
)
from apps.agents.runtime_services import create_invocation_display_context, finish_invocation_display_run
from apps.common.subjects import hash_token
from apps.mobile.models import MobileCommand, MobileDevice


class MobileFlowGuards:
    def test_pair_heartbeat_mcp_approve_dispatch_and_complete(self) -> None:
        create_response = self.client.post(
            "/api/v1/mobile-devices/",
            {"name": "e2e-phone", "approval_mode": MobileDevice.APPROVAL_CONFIRM_HIGH_RISK},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(create_response.status_code, 201, create_response.content)
        device_id = self.mobile_flow_payload(create_response)["id"]
        token = self.mobile_flow_payload(create_response)["pairing_token"]

        heartbeat_response = self.client.post(
            f"/api/v1/mobile-devices/{device_id}/device/heartbeat/",
            {
                "current_package": "com.android.settings",
                "current_activity": "Settings",
                "observation": {
                    "packageName": "com.android.settings",
                    "nodes": [{"text": "Network", "clickable": True}],
                },
                "capabilities": {"accessibility": True, "screen_observation": True},
            },
            format="json",
            HTTP_X_NEXUS_MOBILE_TOKEN=token,
        )
        self.assertEqual(heartbeat_response.status_code, 200, heartbeat_response.content)
        self.assertEqual(self.mobile_flow_payload(heartbeat_response)["online_status"], MobileDevice.ONLINE_ONLINE)

        export_response = self.client.get(
            f"/api/v1/mobile-devices/{device_id}/mcp/export/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(export_response.status_code, 200, export_response.content)
        export_payload = self.mobile_flow_payload(export_response)["mcpServers"]
        self.assertEqual(len(export_payload), 1)
        self.assertIn(f"/api/v1/mobile-devices/{device_id}/mcp/", next(iter(export_payload.values()))["url"])

        initialize_response = self._mcp(device_id, {"jsonrpc": "2.0", "id": "init", "method": "initialize"})
        self.assertEqual(initialize_response["result"]["serverInfo"]["name"], "nexus_mobile")

        tools_response = self._mcp(device_id, {"jsonrpc": "2.0", "id": "tools", "method": "tools/list"})
        tool_names = {tool["name"] for tool in tools_response["result"]["tools"]}
        self.assertIn("mobile_type_text", tool_names)
        self.assertIn("mobile_observe", tool_names)

        observe_response = self._mcp(
            device_id,
            {
                "jsonrpc": "2.0",
                "id": "observe",
                "method": "tools/call",
                "params": {"name": "mobile_observe", "arguments": {}},
            },
        )
        self.assertIn("Network", observe_response["result"]["content"][0]["text"])

        call_response = self._mcp(
            device_id,
            {
                "jsonrpc": "2.0",
                "id": "type",
                "method": "tools/call",
                "params": {"name": "mobile_type_text", "arguments": {"text": "hello"}},
            },
        )
        command_id = call_response["result"]["structuredContent"]["command_id"]
        self.assertEqual(call_response["result"]["structuredContent"]["status"], MobileCommand.STATUS_PENDING_APPROVAL)
        self.assertTrue(AuditLog.objects.filter(action="mobile.mcp.tool.call", resource_id=device_id).exists())

        blocked_poll = self.client.post(
            f"/api/v1/mobile-devices/{device_id}/device/commands/next/",
            HTTP_X_NEXUS_MOBILE_TOKEN=token,
        )
        self.assertEqual(blocked_poll.status_code, 200, blocked_poll.content)
        self.assertIsNone(self.mobile_flow_payload(blocked_poll)["command"])

        approve_response = self.client.post(
            f"/api/v1/mobile-commands/{command_id}/approve/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(approve_response.status_code, 200, approve_response.content)

        poll_response = self.client.post(
            f"/api/v1/mobile-devices/{device_id}/device/commands/next/",
            HTTP_X_NEXUS_MOBILE_TOKEN=token,
        )
        self.assertEqual(poll_response.status_code, 200, poll_response.content)
        self.assertEqual(self.mobile_flow_payload(poll_response)["id"], command_id)
        self.assertEqual(self.mobile_flow_payload(poll_response)["status"], MobileCommand.STATUS_RUNNING)

        complete_response = self.client.post(
            f"/api/v1/mobile-commands/{command_id}/device/result/",
            {"status": MobileCommand.STATUS_SUCCEEDED, "result": {"ok": True}},
            format="json",
            HTTP_X_NEXUS_MOBILE_TOKEN=token,
        )
        self.assertEqual(complete_response.status_code, 200, complete_response.content)
        self.assertEqual(self.mobile_flow_payload(complete_response)["status"], MobileCommand.STATUS_SUCCEEDED)

    def test_agent_runner_calls_mobile_mcp_and_device_completes_command(self) -> None:
        agent = self.create_mobile_flow_agent(
            tenant=self.tenant,
            name="mobile-agent",
            status=Agent.STATUS_ACTIVE,
            current_version="v1",
            created_by=self.owner,
        )
        run = self.create_mobile_flow_run(
            tenant=self.tenant,
            agent=agent,
            title="Mobile tool run",
            write_token="mobile-run-token",
        )
        create_response = self.client.post(
            "/api/v1/mobile-devices/",
            {"name": "agent-phone", "approval_mode": MobileDevice.APPROVAL_AUTO},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(create_response.status_code, 201, create_response.content)
        device_id = self.mobile_flow_payload(create_response)["id"]
        token = self.mobile_flow_payload(create_response)["pairing_token"]

        heartbeat_response = self.client.post(
            f"/api/v1/mobile-devices/{device_id}/device/heartbeat/",
            {
                "current_package": "com.android.settings",
                "current_activity": "Settings",
                "observation": {"nodes": [{"text": "Settings", "clickable": True}]},
                "capabilities": {"accessibility": True, "screen_observation": True},
            },
            format="json",
            HTTP_X_NEXUS_MOBILE_TOKEN=token,
        )
        self.assertEqual(heartbeat_response.status_code, 200, heartbeat_response.content)

        export_response = self.client.get(
            f"/api/v1/mobile-devices/{device_id}/mcp/export/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(export_response.status_code, 200, export_response.content)
        mcp_server = next(iter(self.mobile_flow_payload(export_response)["mcpServers"].values()))
        self.assertEqual(mcp_server["device_id"], device_id)

        tools_response = self._agent_mcp(agent=agent, server=mcp_server, payload={"jsonrpc": "2.0", "id": "tools", "method": "tools/list"})
        self.assertIn("mobile_open_app", {tool["name"] for tool in tools_response["result"]["tools"]})

        call_response = self._agent_mcp(
            agent=agent,
            server=mcp_server,
            payload={
                "jsonrpc": "2.0",
                "id": "agent-call",
                "method": "tools/call",
                "params": {"name": "mobile_open_app", "arguments": {"package": "com.android.settings"}},
            },
        )
        structured = call_response["result"]["structuredContent"]
        command_id = structured["command_id"]
        self.assertEqual(structured["device_id"], device_id)
        self.assertEqual(structured["status"], MobileCommand.STATUS_QUEUED)
        self.assertFalse(structured["requires_approval"])

        AgentDisplayEvent.objects.create(
            tenant=self.tenant,
            agent=agent,
            run=run,
            seq=1,
            event_type=AgentDisplayEvent.TYPE_TOOL_STARTED,
            payload_json={
                "tool": "mobile_open_app",
                "mobile_command_id": command_id,
                "mobile_device_id": device_id,
            },
        )

        poll_response = self.client.post(
            f"/api/v1/mobile-devices/{device_id}/device/commands/next/",
            HTTP_X_NEXUS_MOBILE_TOKEN=token,
        )
        self.assertEqual(poll_response.status_code, 200, poll_response.content)
        self.assertEqual(self.mobile_flow_payload(poll_response)["id"], command_id)
        self.assertEqual(self.mobile_flow_payload(poll_response)["action"], MobileCommand.ACTION_OPEN_APP)
        self.assertEqual(self.mobile_flow_payload(poll_response)["arguments"]["package"], "com.android.settings")

        complete_response = self.client.post(
            f"/api/v1/mobile-commands/{command_id}/device/result/",
            {"status": MobileCommand.STATUS_SUCCEEDED, "result": {"opened": "com.android.settings"}},
            format="json",
            HTTP_X_NEXUS_MOBILE_TOKEN=token,
        )
        self.assertEqual(complete_response.status_code, 200, complete_response.content)
        self.assertEqual(self.mobile_flow_payload(complete_response)["status"], MobileCommand.STATUS_SUCCEEDED)
        self.assertEqual(self.mobile_flow_payload(complete_response)["result"]["opened"], "com.android.settings")

        command = MobileCommand.objects.get(id=command_id)
        self.assertEqual(str(command.device_id), device_id)
        self.assertEqual(command.created_by, self.owner)
        self.assertTrue(
            AgentDisplayEvent.objects.filter(
                agent=agent,
                run=run,
                payload_json__mobile_command_id=command_id,
                payload_json__mobile_device_id=device_id,
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="mobile.mcp.tool.call",
                resource_id=device_id,
                metadata__tool="mobile_open_app",
                metadata__command_id=command_id,
            ).exists()
        )

    def test_agent_mobile_binding_is_caller_private_and_global_binding_is_gone(self) -> None:
        agent = self.create_mobile_flow_agent(
            tenant=self.tenant,
            name="bound-mobile-agent",
            status=Agent.STATUS_ACTIVE,
            current_version="v1",
            visibility=Agent.VISIBILITY_TENANT,
            mobile_requirement=Agent.MOBILE_REQUIRED,
            mobile_capabilities=["mobile.observe", "mobile.type_text", "mobile.open_app"],
            created_by=self.owner,
        )
        create_response = self.client.post(
            "/api/v1/mobile-devices/",
            {"name": "bound-phone", "approval_mode": MobileDevice.APPROVAL_AUTO},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(create_response.status_code, 201, create_response.content)
        device_id = self.mobile_flow_payload(create_response)["id"]
        pairing_token = self.mobile_flow_payload(create_response)["pairing_token"]
        legacy_response = self.client.post(
            f"/api/v1/agents/{agent.id}/mobile-devices/",
            {"device_id": device_id},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assert_legacy_mobile_binding_unavailable(legacy_response)

        grant_response = self.client.put(
            f"/api/v1/agents/{agent.id}/mobile-grant/",
            {"scopes": ["mobile.observe", "mobile.type_text", "mobile.open_app"]},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(grant_response.status_code, 200, grant_response.content)
        self.assertEqual(
            self.mobile_flow_payload(grant_response)["scopes"],
            ["mobile.observe", "mobile.type_text", "mobile.open_app"],
        )

        bind_response = self.client.post(
            f"/api/v1/agents/{agent.id}/mobile-bindings/",
            {"device_id": device_id, "is_default": True},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(bind_response.status_code, 201, bind_response.content)
        binding_id = self.mobile_flow_payload(bind_response)["id"]
        self.assertEqual(self.mobile_flow_payload(bind_response)["device_id"], device_id)
        self.assertTrue(self.mobile_flow_payload(bind_response)["is_default"])

        self.assert_foreign_mobile_binding_denied(agent, binding_id)

        heartbeat_response = self.client.post(
            f"/api/v1/mobile-devices/{device_id}/device/heartbeat/",
            {"online_status": MobileDevice.ONLINE_ONLINE, "capabilities": {"accessibility": True}},
            format="json",
            HTTP_X_NEXUS_MOBILE_TOKEN=pairing_token,
        )
        self.assertEqual(heartbeat_response.status_code, 200, heartbeat_response.content)

        version = AgentVersion.objects.create(
            agent=agent,
            version="v1",
            mobile_requirement=Agent.MOBILE_REQUIRED,
            mobile_capabilities=["mobile.observe", "mobile.type_text", "mobile.open_app"],
            created_by=self.owner,
        )
        deployment = AgentDeployment.objects.create(
            agent=agent,
            version=version,
            env="prod",
            status=AgentDeployment.STATUS_ACTIVE,
            deployed_by=self.owner,
        )
        image = AgentRuntimeImage.objects.create(
            tenant=self.tenant,
            agent=agent,
            image_ref="registry.invalid/mobile-agent:v1",
            created_by=self.owner,
        )
        runtime = AgentRuntimeDeployment.objects.create(
            tenant=self.tenant,
            agent=agent,
            agent_deployment=deployment,
            image=image,
            runtime_kind=AgentRuntimeDeployment.RUNTIME_DOCKER,
            env="prod",
            status=AgentRuntimeDeployment.STATUS_ACTIVE,
            internal_mcp_url="http://runtime.invalid/mcp",
        )
        # AgentVersion remains a historical record. New Runs snapshot the
        # current Agent control-plane policy instead of the deployed version.
        agent.mobile_requirement = Agent.MOBILE_OPTIONAL
        agent.mobile_capabilities = ["mobile.observe"]
        agent.save(update_fields=["mobile_requirement", "mobile_capabilities", "updated_at"])
        invocation_request = APIRequestFactory().post(
            "/mcp",
            {},
            format="json",
            HTTP_ACCEPT="text/event-stream",
        )
        invocation_request.user = self.owner
        invocation_request.tenant_id = str(self.tenant.id)
        invocation_request.project_id = self.mobile_invocation_project()
        invocation_run, invocation_context = create_invocation_display_context(
            runtime=runtime,
            tool_name="inspect_mobile",
            request=invocation_request,
        )
        self.assertEqual(str(invocation_run.mobile_binding_id), binding_id)
        self.assertEqual(
            invocation_run.mobile_capabilities_snapshot,
            ["mobile.observe"],
        )
        self.assertTrue(invocation_context.mobile_enabled)
        self.assertEqual(
            invocation_run.mobile_delegate_token_hash,
            hash_token(invocation_context.mobile_delegate_token),
        )
        finish_invocation_display_run(run=invocation_run, succeeded=True)

        # The remainder of this test exercises an already-snapshotted Run with
        # the full grant and is intentionally independent from version fields.
        agent.mobile_requirement = Agent.MOBILE_REQUIRED
        agent.mobile_capabilities = ["mobile.observe", "mobile.type_text", "mobile.open_app"]
        agent.save(update_fields=["mobile_requirement", "mobile_capabilities", "updated_at"])

        delegate_token = "caller-private-mobile-delegate"
        binding = AgentMobileBinding.objects.get(id=binding_id)
        run = self.create_mobile_flow_run(
            tenant=self.tenant,
            consumer_tenant=self.tenant,
            agent=agent,
            run_kind=AgentDisplayRun.KIND_INVOCATION,
            caller_principal_type="user",
            caller_subject_hash=binding.caller_subject_hash,
            mobile_binding=binding,
            mobile_capabilities_snapshot=["mobile.observe", "mobile.type_text", "mobile.open_app"],
            mobile_delegate_token_hash=hash_token(delegate_token),
            mobile_delegate_token_expires_at=timezone.now() + timedelta(minutes=10),
            interaction_mode="stream",
            write_token="run-write-token",
        )
        status_response = self.client.get(
            f"/api/v1/internal/agent-runs/{run.id}/mobile/",
            HTTP_X_NEXUS_MOBILE_DELEGATE_TOKEN=delegate_token,
        )
        self.assertEqual(status_response.status_code, 200, status_response.content)
        self.assertNotIn("device_id", self.mobile_flow_payload(status_response))
        command_response = self.client.post(
            f"/api/v1/internal/agent-runs/{run.id}/mobile/",
            {
                "client_request_id": "77777777-7777-4777-8777-777777777777",
                "action": "observe",
                "arguments": {},
            },
            format="json",
            HTTP_X_NEXUS_MOBILE_DELEGATE_TOKEN=delegate_token,
        )
        self.assertEqual(command_response.status_code, 201, command_response.content)
        command_id = self.mobile_flow_payload(command_response)["id"]
        command = MobileCommand.objects.get(id=command_id)
        self.assertEqual(command.display_run, run)
        self.assertEqual(command.mobile_binding, binding)
        self.assertEqual(command.caller_subject_hash, binding.caller_subject_hash)
        replay_response = self.client.post(
            f"/api/v1/internal/agent-runs/{run.id}/mobile/",
            {
                "client_request_id": "77777777-7777-4777-8777-777777777777",
                "action": "observe",
                "arguments": {},
            },
            format="json",
            HTTP_X_NEXUS_MOBILE_DELEGATE_TOKEN=delegate_token,
        )
        self.assertEqual(replay_response.status_code, 201, replay_response.content)
        self.assertEqual(self.mobile_flow_payload(replay_response)["id"], command_id)
        self.assertEqual(MobileCommand.objects.filter(display_run=run).count(), 1)
        conflict_response = self.client.post(
            f"/api/v1/internal/agent-runs/{run.id}/mobile/",
            {
                "client_request_id": "77777777-7777-4777-8777-777777777777",
                "action": "open_app",
                "arguments": {"package": "com.example"},
            },
            format="json",
            HTTP_X_NEXUS_MOBILE_DELEGATE_TOKEN=delegate_token,
        )
        self.assertEqual(conflict_response.status_code, 409, conflict_response.content)
        self.assertIn("MOBILE_COMMAND_IDEMPOTENCY_CONFLICT", conflict_response.content.decode("utf-8"))
        hosted_poll = self.client.post(
            f"/api/v1/mobile-devices/{device_id}/device/commands/next/",
            HTTP_X_NEXUS_MOBILE_TOKEN=pairing_token,
        )
        self.assertEqual(hosted_poll.status_code, 200, hosted_poll.content)
        self.assertEqual(self.mobile_flow_payload(hosted_poll)["id"], command_id)
        hosted_complete = self.client.post(
            f"/api/v1/mobile-commands/{command_id}/device/result/",
            {"status": MobileCommand.STATUS_SUCCEEDED, "result": {"nodes": [{"text": "private-run-state"}]}},
            format="json",
            HTTP_X_NEXUS_MOBILE_TOKEN=pairing_token,
        )
        self.assertEqual(hosted_complete.status_code, 200, hosted_complete.content)
        command.refresh_from_db()
        self.assertEqual(command.result["nodes"][0]["text"], "private-run-state")
        binding.device.refresh_from_db()
        self.assertNotIn("private-run-state", json.dumps(binding.device.last_observation))

        run.interaction_mode = "json"
        run.save(update_fields=["interaction_mode", "updated_at"])
        command_count = MobileCommand.objects.filter(display_run=run).count()
        json_only_response = self.client.post(
            f"/api/v1/internal/agent-runs/{run.id}/mobile/",
            {"action": "type_text", "arguments": {"text": "must not execute"}},
            format="json",
            HTTP_X_NEXUS_MOBILE_DELEGATE_TOKEN=delegate_token,
        )
        self.assertEqual(json_only_response.status_code, 409, json_only_response.content)
        self.assertIn("INTERACTIVE_TRANSPORT_REQUIRED", json_only_response.content.decode("utf-8"))
        self.assertEqual(MobileCommand.objects.filter(display_run=run).count(), command_count)
        run.interaction_mode = "stream"
        run.save(update_fields=["interaction_mode", "updated_at"])

        high_risk_response = self.client.post(
            f"/api/v1/internal/agent-runs/{run.id}/mobile/",
            {"action": "type_text", "arguments": {"text": "caller-approved value"}},
            format="json",
            HTTP_X_NEXUS_MOBILE_DELEGATE_TOKEN=delegate_token,
        )
        self.assertEqual(high_risk_response.status_code, 201, high_risk_response.content)
        high_risk_id = self.mobile_flow_payload(high_risk_response)["id"]
        self.assertEqual(self.mobile_flow_payload(high_risk_response)["status"], MobileCommand.STATUS_PENDING_APPROVAL)
        developer_events = self.client.get(
            f"/api/v1/agents/{agent.id}/display-runs/{run.id}/events/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(developer_events.status_code, 200, developer_events.content)
        self.assertNotIn("nexus.mobile.input_required", developer_events.content.decode("utf-8"))
        interaction = AgentRunInteraction.objects.get(run=run, key=f"mobile:{high_risk_id}")
        interaction.status = AgentRunInteraction.STATUS_ANSWERED
        interaction.response_json = {"value": "approve"}
        interaction.answered_at = timezone.now()
        interaction.save(update_fields=["status", "response_json", "answered_at", "updated_at"])
        approved_response = self.client.get(
            f"/api/v1/internal/agent-runs/{run.id}/mobile/commands/{high_risk_id}/",
            HTTP_X_NEXUS_MOBILE_DELEGATE_TOKEN=delegate_token,
        )
        self.assertEqual(approved_response.status_code, 200, approved_response.content)
        self.assertEqual(self.mobile_flow_payload(approved_response)["status"], MobileCommand.STATUS_QUEUED)

        second_delegate = "second-mobile-delegate"
        second_run = self.create_mobile_flow_run(
            tenant=self.tenant,
            consumer_tenant=self.tenant,
            agent=agent,
            run_kind=AgentDisplayRun.KIND_INVOCATION,
            caller_principal_type="user",
            caller_subject_hash=binding.caller_subject_hash,
            mobile_binding=binding,
            mobile_capabilities_snapshot=["mobile.observe"],
            mobile_delegate_token_hash=hash_token(second_delegate),
            mobile_delegate_token_expires_at=timezone.now() + timedelta(minutes=10),
            interaction_mode="stream",
            write_token="second-run-write-token",
        )
        busy_response = self.client.post(
            f"/api/v1/internal/agent-runs/{second_run.id}/mobile/",
            {"action": "observe", "arguments": {}},
            format="json",
            HTTP_X_NEXUS_MOBILE_DELEGATE_TOKEN=second_delegate,
        )
        self.assertEqual(busy_response.status_code, 409, busy_response.content)

        shrink_response = self.client.patch(
            f"/api/v1/agents/{agent.id}/",
            {
                "mobile_requirement": Agent.MOBILE_OPTIONAL,
                "mobile_capabilities": ["mobile.observe"],
            },
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(shrink_response.status_code, 200, shrink_response.content)
        high_risk_command = MobileCommand.objects.get(id=high_risk_id)
        self.assertEqual(high_risk_command.status, MobileCommand.STATUS_CANCELED)
        grant_after_shrink = self.client.get(
            f"/api/v1/agents/{agent.id}/mobile-grant/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(grant_after_shrink.status_code, 200, grant_after_shrink.content)
        self.assertEqual(self.mobile_flow_payload(grant_after_shrink)["scopes"], ["mobile.observe"])
        self.assertFalse(AgentMobileLease.objects.filter(run=run, status="active").exists())

        revoke_response = self.client.delete(
            f"/api/v1/agents/{agent.id}/mobile-grant/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(revoke_response.status_code, 204, revoke_response.content)
        revoked_delegate = self.client.get(
            f"/api/v1/internal/agent-runs/{run.id}/mobile/",
            HTTP_X_NEXUS_MOBILE_DELEGATE_TOKEN=delegate_token,
        )
        self.assertEqual(revoked_delegate.status_code, 404, revoked_delegate.content)

        bad_token_response = self.client.get(
            f"/api/v1/internal/agent-runs/{run.id}/mobile/",
            HTTP_X_NEXUS_MOBILE_DELEGATE_TOKEN="wrong-token",
        )
        self.assertEqual(bad_token_response.status_code, 404, bad_token_response.content)

        export_response = self.client.get(
            f"/api/v1/agents/{agent.id}/mcp/export/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(export_response.status_code, 200, export_response.content)
        self.assertFalse(any(server.get("device_id") for server in self.mobile_flow_payload(export_response)["mcpServers"].values()))

    def test_mobile_coordinates_accept_normalized_and_pixel_spaces(self) -> None:
        from apps.mobile.serializers import MobileCommandCreateSerializer

        normalized = MobileCommandCreateSerializer(
            data={"action": "tap_coordinates", "arguments": {"x": 0.5, "y": 0.75}}
        )
        self.assertTrue(normalized.is_valid(), normalized.errors)
        self.assertEqual(normalized.validated_data["arguments"]["coordinate_space"], "normalized")

        pixels = MobileCommandCreateSerializer(
            data={
                "action": "swipe",
                "arguments": {
                    "start_x": 500,
                    "start_y": 1400,
                    "end_x": 500,
                    "end_y": 400,
                    "coordinate_space": "pixels",
                },
            }
        )
        self.assertTrue(pixels.is_valid(), pixels.errors)
        self.assertEqual(pixels.validated_data["arguments"]["start_y"], 1400)

    def test_rotating_pairing_token_invalidates_old_token(self) -> None:
        create_response = self.client.post(
            "/api/v1/mobile-devices/",
            {"name": "rotated-phone"},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(create_response.status_code, 201, create_response.content)
        device_id = self.mobile_flow_payload(create_response)["id"]
        old_token = self.mobile_flow_payload(create_response)["pairing_token"]

        rotate_response = self.client.post(
            f"/api/v1/mobile-devices/{device_id}/rotate-token/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(rotate_response.status_code, 200, rotate_response.content)
        new_token = self.mobile_flow_payload(rotate_response)["pairing_token"]
        self.assertNotEqual(old_token, new_token)

        old_response = self.client.post(
            f"/api/v1/mobile-devices/{device_id}/device/heartbeat/",
            {"online_status": MobileDevice.ONLINE_ONLINE},
            format="json",
            HTTP_X_NEXUS_MOBILE_TOKEN=old_token,
        )
        self.assertEqual(old_response.status_code, 403)

        new_response = self.client.post(
            f"/api/v1/mobile-devices/{device_id}/device/heartbeat/",
            {"online_status": MobileDevice.ONLINE_ONLINE},
            format="json",
            HTTP_X_NEXUS_MOBILE_TOKEN=new_token,
        )
        self.assertEqual(new_response.status_code, 200, new_response.content)

    def _agent_mcp(self, *, agent: Agent, server: dict, payload: dict) -> dict:
        path = urlsplit(server["url"]).path
        response = self.client.post(
            path,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_NEXUS_TENANT=server["headers"]["X-Nexus-Tenant"],
            HTTP_X_NEXUS_PROJECT=server["headers"].get("X-Nexus-Project", ""),
            HTTP_X_NEXUS_AGENT=str(agent.id),
        )
        self.assertEqual(response.status_code, 200, response.content)
        return json.loads(response.content.decode("utf-8"))

    def _mcp(self, device_id: str, payload: dict) -> dict:
        response = self.client.post(
            f"/api/v1/mobile-devices/{device_id}/mcp/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(response.status_code, 200, response.content)
        return json.loads(response.content.decode("utf-8"))
