import io
import struct
import wave
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from rest_framework import exceptions

from apps.agents.runtime_runner import RuntimeDisplayContext
from apps.agents.edge_serializers import EdgeAgentRegistrationUpsertSerializer
from apps.agents.file_transfers import (
    AudioInputNotSupported,
    FileChangedDuringImport,
    audio_duration_seconds,
    prepare_audio,
)
from apps.agents.runtime_services import (
    _private_run_arguments,
    report_agent_model_usage,
    resolve_execution_profile,
    runtime_headers_with_agui,
)
from apps.agents.tool_catalog import _normalize_tools
from apps.gateway.provider_adapters import ProviderResponse
from apps.gateway.services import attribute_agent_model_usage


class AgentExecutionControlsTest(SimpleTestCase):
    def descriptor(self):
        return {
            "name": "inspect",
            "input_schema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "files": {"type": "array"},
                    "audio": {"type": "array"},
                },
                "required": ["content"],
            },
            "execution_profiles": [{
                "id": "balanced", "label": "Balanced", "model": "gpt-5",
                "reasoning_efforts": ["low", "medium", "high"],
                "default_reasoning_effort": "medium", "context_window": 400000,
            }],
        }

    def test_profile_selection_and_file_arguments_are_server_validated(self):
        profile, effort = resolve_execution_profile(
            descriptor=self.descriptor(), profile_id="balanced", reasoning_effort="high"
        )
        self.assertEqual(profile["model"], "gpt-5")
        self.assertEqual(effort, "high")
        arguments, text = _private_run_arguments(
            descriptor=self.descriptor(), content="Inspect",
            arguments=None,
            file_arguments=[{"file_id": "file-1"}],
            audio_arguments=[{"file_id": "audio-1"}],
        )
        self.assertEqual(text, "Inspect")
        self.assertEqual(arguments["content"], "Inspect")
        self.assertEqual(arguments["audio"][0]["file_id"], "audio-1")
        with self.assertRaises(exceptions.ValidationError):
            resolve_execution_profile(
                descriptor=self.descriptor(), profile_id="balanced", reasoning_effort="ultra"
            )

    def test_publisher_default_and_model_managed_reasoning_are_resolved(self):
        descriptor = {"execution_profiles": [
            {"id": "balanced", "label": "Balanced", "model": "gpt-5",
             "reasoning_efforts": ["low", "medium"], "default_reasoning_effort": "medium"},
            {"id": "fixed", "label": "Fixed", "model": "local-model", "is_default": True,
             "reasoning_efforts": [], "default_reasoning_effort": ""},
        ]}
        profile, effort = resolve_execution_profile(
            descriptor=descriptor, profile_id="", reasoning_effort=""
        )
        self.assertEqual(profile["id"], "fixed")
        self.assertEqual(effort, "")

        normalized = _normalize_tools([
            {**descriptor, "name": "inspect", "task": True}
        ], version_policy={}, edge_manifest=True)[0]["execution_profiles"]
        self.assertFalse(normalized[0]["is_default"])
        self.assertTrue(normalized[1]["is_default"])

    @patch("apps.agents.runtime_services.get_internal_interaction_run")
    def test_reported_usage_cannot_claim_another_execution_profile(self, get_run):
        invocation = SimpleNamespace(execution_profile_id="balanced")
        invocations = MagicMock()
        invocations.filter.return_value.order_by.return_value.first.return_value = invocation
        get_run.return_value = SimpleNamespace(
            run_kind="invocation", status="running", consumer_tenant_id="tenant-1",
            consumer_project_id="project-1", runtime_invocations=invocations,
        )
        with self.assertRaises(exceptions.ValidationError) as mismatch:
            report_agent_model_usage.__wrapped__(
                run_id="run-1", token="secret", tenant_id="tenant-1", project_id="project-1",
                data={"event_id": "usage-1", "turn_index": 1, "profile_id": "fast",
                      "model": "gpt-5-mini", "input_tokens": 1, "output_tokens": 1,
                      "context_window": 128000},
            )
        self.assertIn("selected for this Run turn", str(mismatch.exception.detail["profile_id"]))

    def test_edge_tool_metadata_keeps_verified_profiles_and_slash(self):
        tools = _normalize_tools([{
            **self.descriptor(), "task": True, "slash_command": "inspect",
            "slash_description": "Inspect workspace", "input_modalities": ["text", "audio"],
        }], version_policy={}, edge_manifest=True)
        self.assertEqual(tools[0]["slash_command"], "inspect")
        self.assertEqual(tools[0]["execution_profiles"][0]["id"], "balanced")
        self.assertEqual(tools[0]["input_modalities"], ["text", "audio"])

    def test_edge_registration_accepts_one_explicit_default_and_rejects_ambiguity(self):
        attrs = {
            "transport": "relay", "relay_id": "relay-1", "relay_router_id": "router-1",
            "relay_assignment_id": "assignment-1", "protocols": ["mcp"],
            "capabilities": ["inspect"], "mcp_tools": [{
                "name": "inspect", "intent": "inspect", "task": True,
                "execution_profiles": [
                    {"id": "balanced", "label": "Balanced", "model": "gpt-5",
                     "reasoning_efforts": ["low", "medium"]},
                    {"id": "fixed", "label": "Fixed", "model": "local-model",
                     "is_default": True, "reasoning_efforts": []},
                ],
            }],
        }
        normalized = EdgeAgentRegistrationUpsertSerializer().validate(attrs)
        profiles = normalized["mcp_tools"][0]["execution_profiles"]
        self.assertFalse(profiles[0]["is_default"])
        self.assertTrue(profiles[1]["is_default"])
        self.assertEqual(profiles[1]["default_reasoning_effort"], "")

        attrs["mcp_tools"][0]["execution_profiles"][0]["is_default"] = True
        with self.assertRaises(exceptions.ValidationError):
            EdgeAgentRegistrationUpsertSerializer().validate(attrs)

    def test_reserved_private_display_slash_commands_are_not_published(self):
        tools = _normalize_tools([{
            **self.descriptor(), "name": f"tool_{command}", "task": True,
            "slash_command": command, "slash_description": "Reserved command",
        } for command in ("new", "cancel", "model", "help")], version_policy={}, edge_manifest=True)
        self.assertEqual([tool["slash_command"] for tool in tools], ["", "", "", ""])

    def test_runtime_headers_override_spoofed_execution_context(self):
        headers = runtime_headers_with_agui(
            headers={
                "X-Nexus-Execution-Profile": "forged",
                "X-Nexus-Input-Files": "forged",
                "X-Nexus-Context-Url": "https://attacker.invalid/context",
                "X-Nexus-Context-Token": "forged-context-token",
            },
            context=RuntimeDisplayContext(
                run_id="run-1", events_url="https://cloud/events", write_token="secret",
                usage_url="https://cloud/usage", execution_profile="balanced",
                execution_model="gpt-5", reasoning_effort="medium",
                execution_context_window=400000,
                input_files=({"file_id": "file-1", "source_kind": "computer"},),
                context_url="https://cloud/context",
                context_token="context-secret",
                turn_index=3,
            ),
        )
        self.assertEqual(headers["X-Nexus-Execution-Profile"], "balanced")
        self.assertIn('"file_id":"file-1"', headers["X-Nexus-Input-Files"])
        self.assertEqual(headers["X-Nexus-Context-Url"], "https://cloud/context")
        self.assertEqual(headers["X-Nexus-Context-Token"], "context-secret")
        self.assertEqual(headers["X-Nexus-Run-Turn"], "3")

    def test_protocol_errors_have_stable_public_codes(self):
        with self.assertRaises(AudioInputNotSupported) as unsupported:
            prepare_audio(
                request=None,
                descriptor={"input_schema": {"properties": {}}, "input_modalities": ["text"]},
                references=["ignored"],
                agent_id="ignored",
            )
        self.assertEqual(unsupported.exception.get_codes(), "AUDIO_INPUT_NOT_SUPPORTED")
        changed = FileChangedDuringImport()
        self.assertEqual(changed.get_codes(), "FILE_CHANGED_DURING_IMPORT")

    def test_audio_duration_is_read_from_real_container_metadata(self):
        value = io.BytesIO()
        with wave.open(value, "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(8_000)
            target.writeframes(b"\x00\x00" * 16_000)
        self.assertEqual(audio_duration_seconds(value.getvalue(), "audio/wav"), 2.0)

        webm = b"\x1aE\xdf\xa3" + b"\x2a\xd7\xb1\x83\x0f\x42\x40" + b"\x44\x89\x88" + struct.pack(">d", 301_000.0)
        self.assertEqual(audio_duration_seconds(webm, "audio/webm"), 301.0)

    @patch("apps.agents.runtime_services.report_agent_model_usage")
    def test_gateway_attribution_uses_provider_usage_and_authenticated_run_headers(self, report):
        request = SimpleNamespace(headers={
            "X-Nexus-Agent-Run-Id": "run-1",
            "X-Nexus-Agent-Usage-Token": "short-lived",
            "X-Nexus-Agent-Usage-Event-Id": "model-call-1",
            "X-Nexus-Agent-Context-Window": "400000",
        }, project_id="project-1")
        response = ProviderResponse(raw={}, request_tokens=999, response_tokens=999, total_tokens=1998, model="gpt-5", latency_ms=1)
        attribute_agent_model_usage(
            request=request,
            tenant=SimpleNamespace(id="tenant-1"),
            provider_response=response,
            actual_usage={"prompt_tokens": 12, "completion_tokens": 3},
            event_id="gateway:fallback",
        )
        payload = report.call_args.kwargs
        self.assertEqual(payload["source"], "gateway")
        self.assertEqual(payload["data"]["input_tokens"], 12)
        self.assertEqual(payload["data"]["output_tokens"], 3)
        self.assertEqual(payload["data"]["event_id"], "model-call-1")
