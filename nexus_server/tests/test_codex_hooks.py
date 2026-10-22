from django.test import SimpleTestCase
from apps.agents.codex_hooks import CODEX_HOOK_EVENTS, normalize_codex_hooks
from apps.agents.python_contract import verified_python_catalog
from apps.agents.python_builder import BuildFailure
from apps.agents.tool_catalog import _normalize_tools


class CodexHookDeclarationTests(SimpleTestCase):
    def test_all_events_and_interrupt_deadline(self):
        hooks = normalize_codex_hooks([{"event": e, "input": {}} for e in CODEX_HOOK_EVENTS], {})
        self.assertEqual(len(hooks), 11)
        self.assertEqual(hooks[-1]["timeout"], 1)

    def test_untrusted_metadata_rejected(self):
        for declaration in [
            {"event": "SessionEnd", "input": {}},
            {"event": "Stop", "input": {}, "command": "bad"},
            {"event": "Stop", "input": {}, "server": "other"},
            {"event": "Stop", "input": {}, "matcher": "*"},
            {"event": "Interrupt", "input": {}, "timeout": 4},
        ]:
            with self.subTest(declaration=declaration), self.assertRaises(ValueError):
                normalize_codex_hooks([declaration], {})

    def test_python_verified_catalog_preserves_sdk_declarations(self):
        declaration = {"event": "Stop", "input": {"active": "${stop_hook_active}"}}
        raw = [{"name": "hook", "inputSchema": {"type": "object", "required": ["active"]},
                "_meta": {"nexus": {"codex_hooks": [declaration]}}}]
        tools, policies, _ = verified_python_catalog(raw)
        self.assertEqual(tools[0]["codex_hooks"][0]["event"], "Stop")
        self.assertIn("codex_hooks", policies["hook"])
        raw[0]["_meta"]["nexus"]["codex_hooks"][0]["input"] = {}
        with self.assertRaises(BuildFailure):
            verified_python_catalog(raw)

    def test_legacy_invalid_catalog_disables_hooks_without_breaking_tools(self):
        tools = _normalize_tools([{"name": "hook", "input_schema": {},
                                  "codex_hooks": [{"event": "SessionEnd", "input": {}}]}],
                                 version_policy={}, edge_manifest=False)
        self.assertEqual(tools[0]["codex_hooks"], [])
        self.assertTrue(tools[0]["codex_hooks_invalid"])

    def test_duplicate_and_oversized_mapping_rejected(self):
        hook = {"event": "Stop", "input": {}}
        for value in [[hook, hook], [{"event": "Stop", "input": {"x": "a" * 8193}}]]:
            with self.assertRaises(ValueError):
                normalize_codex_hooks(value, {})

    def test_edge_registration_preserves_and_validates_hooks(self):
        from apps.agents.edge_serializers import EdgeAgentRegistrationUpsertSerializer
        payload = {"origin": "agent://demo/hook", "route_id": "test", "protocols": ["mcp"],
                   "capabilities": ["hook"], "generation": 1, "transport": "relay",
                   "relay_id": "relay", "relay_router_id": "router", "relay_assignment_id": "assignment",
                   "mcp_tools": [{"name": "hook", "intent": "hook", "intent_version": 1,
                                 "input_schema": {"type": "object"},
                                 "codex_hooks": [{"event": "Stop", "input": {}}]}]}
        serializer = EdgeAgentRegistrationUpsertSerializer(data=payload)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["mcp_tools"][0]["codex_hooks"][0]["event"], "Stop")
        payload["mcp_tools"][0]["codex_hooks"][0]["event"] = "SessionEnd"
        serializer = EdgeAgentRegistrationUpsertSerializer(data=payload)
        self.assertFalse(serializer.is_valid())
