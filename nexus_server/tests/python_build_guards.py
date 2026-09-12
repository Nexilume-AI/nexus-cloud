"""Original Python upload/inspection guards, without commercial host fixtures.

Mocked builder outcomes are control-plane tests, NOT real Docker acceptance.
"""
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework import exceptions
from apps.agents.models import AgentPythonBuild, AgentRuntimeImage
from apps.agents.python_builds import inspect_source, validate_requirements, claim_build, execute_build, runtime_secrets
from apps.agents.python_builder import ensure_builder_dependencies, sandbox, save_image_artifact

SOURCE = '''from nexus_agent.fastmcp import NexusMCPServer
server = NexusMCPServer("Example")
@server.tool
def hello(message: str) -> str:
    return "hello " + message
'''
DIGEST = "sha256:" + "a" * 64


class SourceInspectionGuards:
    @override_settings(NEXUS_AGENT_PYTHON_BASE_IMAGE=DIGEST)
    @mock.patch("apps.agents.python_builder.docker")
    def test_builder_readiness_requires_live_docker_and_profile(self, docker):
        docker.side_effect = [b"26.1", json.dumps([{"Id": DIGEST}]).encode()]
        ensure_builder_dependencies()
        self.assertEqual(docker.call_count, 2)

    def test_nexus_instance_detected_without_executing(self):
        self.assertEqual(inspect_source("raise RuntimeError('never execute')\n" + SOURCE), {"entrypoint": "server", "framework": "NexusMCPServer"})

    def test_native_fastmcp_and_alias(self):
        self.assertEqual(inspect_source('from fastmcp import FastMCP as F\nmcp = F("test")')["entrypoint"], "mcp")
        self.assertEqual(inspect_source('import fastmcp as f\nmcp = f.FastMCP("test")')["framework"], "FastMCP")

    def test_missing_and_ambiguous_server(self):
        for source in ['print("hello")', SOURCE + '\nsecond = NexusMCPServer("Second")']:
            with self.assertRaises(exceptions.ValidationError):
                inspect_source(source)
        self.assertEqual(inspect_source(SOURCE + '\nsecond = NexusMCPServer("Second")', "second")["entrypoint"], "second")

    def test_syntax_error_never_quotes_source(self):
        with self.assertRaises(exceptions.ValidationError) as raised:
            inspect_source('x = "secret-never-echo')
        self.assertNotIn("secret-never-echo", str(raised.exception))
        self.assertIn("line 1", str(raised.exception))

    def test_requirements_reject_options_urls_paths_and_platform_overrides(self):
        for value in ["-r other.txt", "--index-url https://secret:password@host", "git+https://repo", "x @ https://example.org/x.whl", "../x", "fastmcp==2", "nexus_agent_sdk", "pip", "x;python_version>'3'"]:
            with self.subTest(value=value), self.assertRaises(exceptions.ValidationError):
                validate_requirements(value)
        self.assertEqual(validate_requirements("# hello\nhttpx>=0.28,<1\nrich==15.0.0"), "httpx>=0.28,<1\nrich==15.0.0")

    def test_sandbox_has_no_host_mount_or_socket_and_is_bounded_offline(self):
        args = sandbox("test", DIGEST, ["verify"])
        for item in ["--read-only", "--cap-drop", "ALL", "--pids-limit", "--memory", "--cpus", "--user", "none"]:
            self.assertIn(item, args)
        self.assertNotIn("--privileged", args)
        self.assertNotIn("-v", args)

    def test_verified_image_artifact_is_written_atomically_under_shared_storage(self):
        import tempfile
        from pathlib import Path

        build = SimpleNamespace(
            pk=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            agent=SimpleNamespace(tenant_id=uuid.uuid4()),
        )

        class CompletedProcess:
            returncode = 0

            @staticmethod
            def poll():
                return 0

        def start(command, **_kwargs):
            self.assertIsInstance(command, list)
            target = Path(command[command.index("--output") + 1])
            target.write_bytes(b"verified-image")
            return CompletedProcess()

        with tempfile.TemporaryDirectory() as root, \
             override_settings(NEXUS_AGENT_STORAGE_ROOT=root), \
             mock.patch("apps.agents.python_builder.subprocess.Popen", side_effect=start):
            relative = save_image_artifact(build, DIGEST)
            target = Path(root) / relative
            self.assertEqual(target.read_bytes(), b"verified-image")
            self.assertFalse(target.with_suffix(".partial").exists())

class PythonBuildHTTPGuards:
    def upload(self, source=SOURCE, **extra):
        return self.client.post(self.url, {"file": SimpleUploadedFile("agent.py", source.encode()), **extra}, format="multipart", **self.headers)

    def test_upload_is_async_private_and_never_executes_docker(self):
        with mock.patch("apps.agents.python_builder.build_image") as builder:
            response = self.upload(secrets=json.dumps({"MODEL_API_KEY": "sensitive-123"}))
        self.assertEqual(response.status_code, 202, response.content)
        builder.assert_not_called()
        build = AgentPythonBuild.objects.get(agent=self.agent)
        self.assertEqual(build.status, "queued")
        self.assertNotIn("sensitive-123", build.encrypted_secrets)
        response = self.client.get(self.url, **self.headers)
        self.assertEqual(response.status_code, 200)
        for secret in ["sensitive-123", "encrypted_secrets", "return \\\"hello"]:
            self.assertNotIn(secret, response.content.decode())
        self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_rejects_duplicate_pending_build(self):
        self.assertEqual(self.upload().status_code, 202)
        self.assertEqual(self.upload().status_code, 400)
        self.assertEqual(AgentPythonBuild.objects.count(), 1)

    def test_invalid_source_and_reserved_secrets_are_field_errors(self):
        for data in [{"source": 'print("nothing")'}, {"secrets": '{"NEXUS_AGUI_TOKEN":"spoof"}'}, {"secrets": '{"PYTHONPATH":"/bad"}'}]:
            self.assertEqual(self.upload(**data).status_code, 400)
        self.assertFalse(AgentPythonBuild.objects.exists())

    def test_size_and_utf8_limits(self):
        self.assertEqual(self.upload("x" * (1024 * 1024 + 1)).status_code, 400)
        response = self.client.post(self.url, {"file": SimpleUploadedFile("agent.py", b"\xff")}, format="multipart", **self.headers)
        self.assertEqual(response.status_code, 400)

    @override_settings(NEXUS_AGENT_PYTHON_BUILDS_ENABLED=False)
    def test_disabled_is_explicit(self):
        self.assertEqual(self.upload().status_code, 400)


class PythonBuildQueueGuards(PythonBuildHTTPGuards):
    """Requires actual PostgreSQL advisory locks; never run on a SQLite stand-in."""
    def test_success_creates_candidate_without_changing_current_image(self):
        old = AgentRuntimeImage.objects.create(agent=self.agent, tenant=self.tenant, image_ref="old:v1")
        self.agent.current_image = old
        self.agent.save()
        self.upload(secrets='{"MODEL_API_KEY":"private-model-key"}')
        build = claim_build("test")
        with mock.patch("apps.agents.python_builder.build_image", return_value={"digest": DIGEST, "image_ref": "candidate:v2", "tool_count": 1, "dependencies": ["httpx==0.28.1"]}):
            execute_build(build)
            execute_build(build)  # Repeated delivery cannot create another image.
        build.refresh_from_db(); self.agent.refresh_from_db()
        self.assertEqual(build.status, "succeeded", build.error_message)
        self.assertEqual(self.agent.current_image_id, old.id)
        self.assertEqual(AgentRuntimeImage.objects.count(), 2)
        self.assertEqual(runtime_secrets(build.image), {"MODEL_API_KEY": "private-model-key"})
        self.assertEqual(runtime_secrets(old), {})

    def test_success_persists_shared_recovery_artifact(self):
        self.upload()
        build = claim_build("test")
        with mock.patch(
            "apps.agents.python_builder.build_image",
            return_value={
                "digest": DIGEST,
                "image_ref": "candidate:v2",
                "artifact_path": "tenant/agent/runtime-images/build/agent-image.tar",
                "tool_count": 1,
                "dependencies": [],
            },
        ):
            execute_build(build)
        build.refresh_from_db()
        self.assertEqual(
            build.image.artifact_path,
            "tenant/agent/runtime-images/build/agent-image.tar",
        )

    def test_private_interactor_catalog_uses_deployed_image_not_latest_candidate(self):
        from apps.agents.tool_catalog import effective_mcp_tools, _current_version
        from apps.agents.models import AgentRuntimeDeployment, AgentVersion
        legacy = AgentVersion.objects.create(agent=self.agent, version="old", artifact_metadata={})
        self.assertEqual(_current_version(self.agent), legacy)
        self.upload()
        build = claim_build("test")
        tools = [{"name": "hello", "description": "Respond to a message", "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}}}}]
        with mock.patch("apps.agents.python_builder.build_image", return_value={"digest": DIGEST, "image_ref": "python:v1", "tool_count": 1, "dependencies": [], "tools": tools, "policies": {"hello": {"chat": True}}}):
            execute_build(build)
        build.refresh_from_db()
        self.assertEqual(_current_version(self.agent), legacy)
        runtime = AgentRuntimeDeployment(agent=self.agent, image=build.image, runtime_kind="docker")
        catalog = effective_mcp_tools(agent=self.agent, runtime=runtime)
        self.assertEqual(catalog[0]["name"], "hello")
        self.assertEqual(catalog[0]["description"], "Respond to a message")
        self.assertIn("message", catalog[0]["input_schema"]["properties"])
        self.assertTrue(catalog[0]["chat"])
        self.assertEqual(effective_mcp_tools(agent=self.agent), [])

    def test_failure_never_persists_exception_secrets_or_replaces_image(self):
        self.upload()
        build = claim_build("test")
        with mock.patch("apps.agents.python_builder.build_image", side_effect=RuntimeError("secret-password")):
            execute_build(build)
        build.refresh_from_db()
        self.assertEqual(build.status, "failed")
        self.assertNotIn("secret-password", build.error_message)
        self.assertFalse(AgentRuntimeImage.objects.exists())

    def test_worker_loss_closes_run_and_releases_pending_slot(self):
        self.upload()
        build = claim_build("lost-worker")
        AgentPythonBuild.objects.filter(pk=build.pk).update(lease_expires_at=timezone.now()-timedelta(seconds=1))
        self.assertIsNone(claim_build("new-worker"))
        build.refresh_from_db()
        self.assertEqual(build.error_code, "BUILD_WORKER_LOST")
        self.assertEqual(self.upload().status_code, 202)
