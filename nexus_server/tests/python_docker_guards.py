"""Original real Docker build assertions shared across edition hosts."""
import uuid
from types import SimpleNamespace
from django.conf import settings
from apps.agents.python_builds import inspect_source
from apps.agents.python_builder import BuildFailure, build_image
from tests.python_build_guards import SOURCE
from tests.python_docker_qa import resolve_profile, remove_owned_image


class PythonDockerGuards:
    def build(self, source, requirements=""):
        base = resolve_profile(settings.NEXUS_AGENT_PYTHON_DOCKER)
        declaration = inspect_source(source)
        build = SimpleNamespace(pk=uuid.uuid4(), agent_id=uuid.uuid4(), base_image=base, source=source, requirements=requirements, **declaration)
        self.addCleanup(remove_owned_image, settings.NEXUS_AGENT_PYTHON_DOCKER, build.pk, build.agent_id)
        return build_image(build, lambda stage: None)

    def test_nexus_sdk_real_container_no_tools_called(self):
        result = self.build(SOURCE.replace('return "hello " + message', 'raise RuntimeError("Never call a tool during validation")'))
        self.assertEqual(result["tool_count"], 1)
        self.assertTrue(result["digest"].startswith("sha256:"))
        self.assertTrue(any(p.startswith("nexus-agent-sdk==") for p in result["dependencies"]))

    def test_native_fastmcp_and_dependencies(self):
        result = self.build(SOURCE.replace("from nexus_agent.fastmcp import NexusMCPServer", "from fastmcp import FastMCP as NexusMCPServer") + "\nimport humanize\n", "humanize==4.15.0")
        self.assertEqual(result["tool_count"], 1)
        self.assertIn("humanize==4.15.0", result["dependencies"])

    def test_native_edge_source_verified_offline_without_router_or_tool_execution(self):
        source = '''from nexus_agent import NexusAgent, NexusRunContext, McpToolDescriptor
agent = NexusAgent(computer_requirement="optional", workspace_capabilities=("files.read",))
@agent.capability("inspect", tool=McpToolDescriptor(name="inspect", task=True, continuable=True))
def inspect(payload, ctx: NexusRunContext):
    raise RuntimeError("Verification must not run this tool")
if __name__ == "__main__":
    agent.run()
'''
        result = self.build(source)
        self.assertEqual(result["tool_count"], 1)
        self.assertTrue(result["policies"]["inspect"]["continuable"])
        self.assertEqual(result["agent_contract"]["computer"], {
            "requirement": "optional", "workspace_capabilities": ["files.read"],
        })

    def test_no_tools_is_actionable(self):
        with self.assertRaises(BuildFailure) as raised:
            self.build('from fastmcp import FastMCP\nserver = FastMCP("Empty")')
        self.assertEqual(raised.exception.code, "NO_TOOLS")
