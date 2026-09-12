from types import SimpleNamespace
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase
from rest_framework import exceptions

from apps.agents.python_builds import inspect_source
from apps.agents.python_builder import BuildFailure
from apps.agents.python_contract import verified_python_catalog, activate_python_contract


CONTRACT = {
    "computer": {"requirement": "required", "workspace_capabilities": ["files.read"]},
    "mobile": {"requirement": "required", "mobile_capabilities": ["mobile.observe"]},
}


class DualRuntimeBuildTests(SimpleTestCase):
    def test_attached_browser_example_has_an_uploadable_top_level_agent(self):
        source = (Path(__file__).resolve().parents[2] / "nexus_openwrt" / "sdk" /
                  "nexus-agent-sdk-python" / "examples" / "browser_session_agent.py").read_text(encoding="utf-8")
        self.assertEqual(inspect_source(source), {"entrypoint": "agent", "framework": "NexusAgent"})

    def test_native_source_detected_without_execution(self):
        for imports, call in [
            ("from nexus_agent import NexusAgent", "NexusAgent"),
            ("from nexus_agent import NexusAgent as Agent", "Agent"),
            ("import nexus_agent as nexus", "nexus.NexusAgent"),
            ("import nexus_agent.agent", "nexus_agent.agent.NexusAgent"),
        ]:
            with self.subTest(imports=imports):
                source = f'{imports}\nagent = {call}(router="auto")\nraise RuntimeError("do not execute")'
                self.assertEqual(inspect_source(source), {"entrypoint": "agent", "framework": "NexusAgent"})

    def test_mixed_multiple_instances_require_explicit_choice(self):
        source = 'from nexus_agent import NexusAgent\nfrom fastmcp import FastMCP\nagent = NexusAgent()\nserver = FastMCP("test")'
        with self.assertRaises(exceptions.ValidationError):
            inspect_source(source)
        self.assertEqual(inspect_source(source, "agent")["framework"], "NexusAgent")

    def test_verified_metadata_preserves_the_full_policy(self):
        policy = {"task": True, "continuable": True, "interactive": True, "recovery_protocol": 1,
                  "mobile_scopes": ["mobile.observe"], "input_modalities": ["text"],
                  "slash_command": "inspect", "slash_description": "Inspect the workspace",
                  "execution_profiles": [{"id": "balanced", "label": "Balanced", "model": "test-model",
                      "reasoning_efforts": ["low"], "default_reasoning_effort": "low", "context_window": 4096}],
                  "agent_contract": CONTRACT}
        raw = [{"name": "inspect", "inputSchema": {"type": "object", "properties": {"content": {"type": "string"}}}, "_meta": {"nexus": policy}}]
        tools, policies, contract = verified_python_catalog(raw, native=True)
        self.assertEqual(contract, CONTRACT)
        self.assertTrue(tools[0]["continuable"])
        self.assertEqual(tools[0]["recovery_protocol"], 1)
        self.assertEqual(tools[0]["slash_command"], "inspect")
        self.assertEqual(tools[0]["execution_profiles"][0]["context_window"], 4096)
        self.assertNotIn("agent_contract", policies["inspect"])

    def test_missing_or_inconsistent_native_contract_is_rejected(self):
        for contract in [None, {}, {**CONTRACT, "computer": {"requirement": "disabled", "workspace_capabilities": ["files.read"]}}]:
            with self.subTest(contract=contract), self.assertRaises(BuildFailure) as raised:
                verified_python_catalog([{"name": "test", "_meta": {"nexus": {"agent_contract": contract}}}], native=True)
            self.assertEqual(raised.exception.code, "AGENT_CONTRACT_INVALID")
        with self.assertRaises(BuildFailure):
            verified_python_catalog([
                {"name": "one", "_meta": {"nexus": {"agent_contract": CONTRACT}}},
                {"name": "two", "_meta": {"nexus": {"agent_contract": {**CONTRACT, "mobile": {"requirement": "disabled", "mobile_capabilities": []}}}}},
            ], native=True)

    def test_legacy_mcp_does_not_change_resource_declarations(self):
        tools, _, contract = verified_python_catalog([{"name": "hello"}])
        self.assertEqual(tools[0]["name"], "hello")
        self.assertIsNone(contract)
        with mock.patch("apps.agents.python_contract.Agent.objects.filter") as query:
            activate_python_contract(agent=SimpleNamespace(pk="agent"), version=SimpleNamespace(artifact_metadata={"source_type": "python_upload"}))
        query.assert_not_called()

    def test_activation_copies_declared_resources_not_caller_grants(self):
        agent = SimpleNamespace(pk="agent")
        version = SimpleNamespace(artifact_metadata={"source_type": "python_upload", "agent_contract": CONTRACT})
        with mock.patch("apps.agents.python_contract.Agent.objects.filter") as query:
            activate_python_contract(agent=agent, version=version)
        query.return_value.update.assert_called_once_with(computer_requirement="required", workspace_capabilities=["files.read"],
            mobile_requirement="required", mobile_capabilities=["mobile.observe"])
        self.assertEqual(agent.computer_requirement, "required")
