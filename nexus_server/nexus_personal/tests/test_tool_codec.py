"""Shared, nonfinancial configuration transforms under private-import denial."""
from types import SimpleNamespace
from django.test import SimpleTestCase
from apps.workspaces import tool_codec as codec
from apps.workspaces.connection_core import WorkspaceError


class PersonalToolCodecTests(SimpleTestCase):
    def test_router_configuration_preserves_external_mcp_and_round_trips(self):
        original = {"mcp_servers": {"external": {"url": "https://external.example/mcp"}},
                    "model_providers": {"external": {"name": "Kept"}}, "custom": "中文\\path"}
        request = SimpleNamespace(build_absolute_uri=lambda path: "https://personal.example" + path)
        updated = codec._configure_router_api_with_token(request=request, config=original,
            router=None, models=["selected-model"], token="credential-memory-sentinel")
        self.assertNotIn("model", original)
        self.assertEqual(updated["mcp_servers"], original["mcp_servers"])
        self.assertEqual(updated["model_providers"]["external"], original["model_providers"]["external"])
        self.assertEqual(updated["model_providers"]["nexus"]["wire_api"], "responses")
        self.assertEqual(codec.parse_codex_toml(codec.dump_toml(updated)), updated)
        self.assertEqual(codec._provider_token(updated), "credential-memory-sentinel")

    def test_agent_add_and_token_rewrite_preserve_api_and_external_servers(self):
        original = {"model": "keep-model", "mcp_servers": {"external": {
            "url": "https://external.example/mcp", "http_headers": {"Authorization": "external-secret"}}}}
        exported = {"server_name": "agent-1", "mcpServers": {"agent-1": {
            "url": "https://personal.example/mcp", "headers": {"X-Context": "value"}}}}
        updated, name = codec._configure_agent_server(config=original, exported=exported, token="old-token")
        self.assertEqual(name, "agent-1")
        rewritten = codec._rewrite_managed_agent_tokens(config=updated, managed={name: {}}, token="new-token")
        self.assertEqual(rewritten["model"], original["model"])
        self.assertEqual(rewritten["mcp_servers"]["external"], original["mcp_servers"]["external"])
        self.assertEqual(codec._mcp_token(rewritten["mcp_servers"][name]), "new-token")
        self.assertEqual(codec._mcp_token(updated["mcp_servers"][name]), "old-token")
        self.assertNotIn(name, original["mcp_servers"])
        self.assertEqual(rewritten["mcp_servers"][name]["http_headers"]["X-Context"], "value")

    def test_summary_and_compatibility_content_do_not_return_secrets(self):
        parsed = {"model_provider": "nexus", "model_providers": {"nexus": {
            "base_url": "https://personal.example", "experimental_bearer_token": "api-secret"}},
            "mcp_servers": {"agent": {"http_headers": {"Authorization": "Bearer agent-secret"}}},
            "nested": {"password": "password-secret", "api-key": "other-secret"}}
        result = codec.codex_config_summary(path="config.toml", exists=True,
            content=codec.dump_toml(parsed), parsed=parsed)
        for secret in ("api-secret", "agent-secret", "password-secret", "other-secret"):
            self.assertNotIn(secret, str(result))
        self.assertEqual(result["content"], result["redacted_content"])
        self.assertTrue(result["has_bearer_token"])

    def test_invalid_toml_is_rejected_and_revision_detects_external_edits(self):
        with self.assertRaises(WorkspaceError):
            codec.parse_codex_toml("[broken")
        self.assertEqual(codec.tool_config_revision("same"), codec.tool_config_revision("same"))
        self.assertNotEqual(codec.tool_config_revision("same"), codec.tool_config_revision("different"))
