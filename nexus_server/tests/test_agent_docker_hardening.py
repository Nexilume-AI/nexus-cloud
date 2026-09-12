"""Docker contract tests: no daemon, credentials, or application data required."""
from types import SimpleNamespace
from unittest.mock import patch
import json
import tempfile
from pathlib import Path

from django.test import SimpleTestCase, override_settings
from rest_framework import exceptions

from apps.agents import runtime_runner as runner
from apps.agents.docker_policy import resource_limits, assert_host, verify_admission


class DockerHealthContractTests(SimpleTestCase):
    def check(self, body, status=200, headers=None):
        response = runner.RuntimeMCPResult(status, headers or {}, body)
        with patch.object(runner, "_http_request", return_value=response), patch.object(runner.time, "sleep"):
            return runner._wait_for_mcp(url="http://runtime.invalid/mcp", attempts=1)

    def test_rejects_jsonrpc_error(self):
        self.assertFalse(self.check(b'{"jsonrpc":"2.0","id":"health","error":{"code":-32603}}'))

    def test_rejects_html_and_empty_success(self):
        for body in (b"<html>proxy error</html>", b"", b"{}"):
            with self.subTest(body=body):
                self.assertFalse(self.check(body))

    def test_rejects_redirect(self):
        self.assertFalse(self.check(b"", status=302))

    def test_health_transport_disables_redirects_and_bounds_session_cleanup(self):
        response = runner.RuntimeMCPResult(200, {"Mcp-Session-Id": "test"}, b"{}")
        with patch.object(runner, "_http_request", return_value=response) as request, patch.object(runner.time, "sleep"):
            self.assertFalse(runner._wait_for_mcp(url="http://runtime.invalid/mcp", attempts=1))
        self.assertEqual(len(request.call_args_list), 2)
        for call in request.call_args_list:
            self.assertFalse(call.kwargs["follow_redirects"])
            self.assertEqual(call.kwargs["max_response_bytes"], 65536)

    def test_accepts_valid_initialize_json_and_sse(self):
        payload = json.dumps({"jsonrpc": "2.0", "id": "health", "result": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "serverInfo": {"name": "agent", "version": "1"},
        }}).encode()
        self.assertTrue(self.check(payload))
        self.assertTrue(self.check(b"event: message\ndata: " + payload + b"\n\n",
                                   headers={"Content-Type": "text/event-stream"}))


@override_settings(NEXUS_AGENT_RUNTIME_MEMORY_LIMIT="512m")
class DockerResourceContractTests(SimpleTestCase):
    def test_invalid_resources_are_rejected(self):
        for cpu, memory in [("NaN", "1Gi"), ("Infinity", "1Gi"), ("-1", "1Gi"), ("0", "1Gi"), ("1", "0Mi"), ("1", "--privileged")]:
            with self.subTest(cpu=cpu, memory=memory), self.assertRaises(exceptions.ValidationError):
                resource_limits(SimpleNamespace(agent=SimpleNamespace(resource_config=SimpleNamespace(cpu=cpu, memory=memory))))

    def test_safety_flags_and_immutable_image(self):
        deployment = SimpleNamespace(id="runtime1", tenant_id="tenant1", project_id=None, agent_id="agent1",
            image=SimpleNamespace(version=None, image_ref="example/agent:mutable", image_digest="sha256:" + "a" * 64),
            agent=SimpleNamespace(resource_config=None))
        with patch.object(runner, "_docker_inspect", return_value=None), patch.object(runner, "_run_docker", return_value="container") as docker, \
             patch.object(runner, "_docker_host_port", return_value=49001), patch.object(runner, "_wait_for_mcp", return_value=True):
            runner.DockerAgentRuntimeRunner().start(deployment=deployment)
        args = next(call.args[0] for call in docker.call_args_list if call.args[0][1] == "run")
        for flag in ["--cpus", "--memory", "--memory-swap", "--user", "--cap-drop", "--read-only", "--restart", "--log-opt", "--label"]:
            self.assertIn(flag, args)
        self.assertNotIn("--rm", args)
        self.assertNotEqual(args[args.index("--network") + 1], "bridge")
        self.assertEqual(args[-1], deployment.image.image_digest)

    def test_declared_resources_reach_docker(self):
        deployment = SimpleNamespace(
            id="auditruntime", tenant_id="audit", project_id=None, agent_id="agent",
            image=SimpleNamespace(version=None, image_ref="example/agent:v1", image_digest="sha256:" + "a" * 64),
            agent=SimpleNamespace(resource_config=SimpleNamespace(cpu="500m", memory="2Gi")),
        )
        with patch.object(runner, "_run_docker", return_value="container") as command, \
             patch.object(runner, "_docker_host_port", return_value=49999), \
             patch.object(runner, "_wait_for_mcp", return_value=True), \
             patch.object(runner, "_docker_inspect", return_value=None):
            runner.DockerAgentRuntimeRunner().start(deployment=deployment)
        args = next(call.args[0] for call in command.call_args_list if call.args[0][1] == "run")
        self.assertIn("--cpus", args)
        self.assertEqual(args[args.index("--cpus") + 1], "0.5")
        self.assertEqual(int(args[args.index("--memory") + 1]), 2 * 1024 ** 3)

    def test_failed_port_lookup_cleans_container(self):
        deployment = SimpleNamespace(
            id="auditruntime", tenant_id="audit", project_id=None, agent_id="agent",
            image=SimpleNamespace(version=None, image_ref="example/agent:v1", image_digest="sha256:" + "a" * 64),
            agent=SimpleNamespace(resource_config=None),
        )
        with patch.object(runner, "_run_docker", return_value="container"), \
             patch.object(runner, "_docker_host_port", side_effect=exceptions.APIException("missing port")), \
             patch.object(runner, "_docker_stop") as stop, \
             patch.object(runner, "_docker_inspect", return_value=None):
            with self.assertRaises(exceptions.APIException):
                runner.DockerAgentRuntimeRunner().start(deployment=deployment)
        self.assertEqual(stop.call_args.args[0], "container")
        self.assertEqual(stop.call_args.kwargs["expected"]["nexus.agent.runtime"], "auditruntime")

    def test_stop_failure_is_not_success(self):
        with patch.object(runner, "_docker_inspect", return_value={"Id": "x"}), \
             patch.object(runner, "_run_docker", side_effect=exceptions.APIException("stop failed")):
            with self.assertRaises(exceptions.APIException):
                runner._docker_stop("x")

    def test_network_ownership_is_verified_before_removal(self):
        with patch.object(runner, "_docker_inspect", return_value={"Labels": {"nexus.managed": "other"}}), \
             patch.object(runner, "_run_docker") as docker:
            with self.assertRaises(exceptions.APIException):
                runner._docker_remove_network("nexus-agent-a-net", {"nexus.managed": "agent"})
        docker.assert_not_called()

    @override_settings(NEXUS_PRODUCTION=True, NEXUS_AGENT_RUNTIME_HOST_ID="worker1", NEXUS_AGENT_RUNTIME_EGRESS_POLICY_READY=False)
    def test_production_requires_egress_policy(self):
        with self.assertRaisesMessage(exceptions.APIException, "AGENT_EGRESS_POLICY_REQUIRED"):
            assert_host()

    @override_settings(NEXUS_PRODUCTION=True, NEXUS_AGENT_RUNTIME_HOST_ID="worker1",
                       NEXUS_AGENT_RUNTIME_EGRESS_POLICY_READY=False, NEXUS_AGENT_IMAGE_ADMISSION_COMMAND=[])
    def test_revoked_admission_does_not_prevent_stopping_owned_container(self):
        assert_host(SimpleNamespace(docker_lifecycle={"host_id": "worker1"}), starting=False)
        with self.assertRaisesMessage(exceptions.APIException, "AGENT_DOCKER_HOST_MISMATCH"):
            assert_host(SimpleNamespace(docker_lifecycle={"host_id": "other"}), starting=False)

    @override_settings(NEXUS_PRODUCTION=True, NEXUS_AGENT_IMAGE_ADMISSION_COMMAND=[])
    def test_production_requires_image_admission(self):
        with self.assertRaisesMessage(exceptions.ValidationError, "AGENT_IMAGE_ADMISSION_REQUIRED"):
            verify_admission("sha256:" + "a" * 64)

    @override_settings(NEXUS_AGENT_IMAGE_ADMISSION_COMMAND=["scanner", "verify"])
    def test_scanner_receives_digest_without_shell_and_rejection_is_redacted(self):
        with patch("apps.agents.docker_policy.verifier_exit_code", return_value=1) as scan:
            with self.assertRaises(exceptions.ValidationError) as error:
                verify_admission("sha256:" + "a" * 64)
        self.assertNotIn("secret", str(error.exception))
        self.assertEqual(scan.call_args.args[0], ["scanner", "verify", "sha256:" + "a" * 64])
        self.assertNotIn("shell", scan.call_args.kwargs)

    @override_settings(NEXUS_AGENT_IMAGE_ADMISSION_COMMAND=["scanner", "verify"])
    def test_admission_rejects_non_digest_inputs_before_launch(self):
        for digest in ("latest", "--help", "sha256:" + "a" * 63, "sha256:" + "A" * 64, None):
            with self.subTest(digest=digest), patch("apps.agents.docker_policy.verifier_exit_code", return_value=0) as scan:
                with self.assertRaisesMessage(exceptions.ValidationError, "AGENT_IMAGE_IDENTITY_INVALID"):
                    verify_admission(digest)
                scan.assert_not_called()

    def test_admission_rejects_unbounded_or_control_character_argv(self):
        for command in (["scanner"] * 33, ["scanner", "a" * 2049], ["scanner", "a\nb"], ["scanner", "a\x00b"]):
            with self.subTest(command_length=len(command)), override_settings(NEXUS_AGENT_IMAGE_ADMISSION_COMMAND=command), \
                 patch("apps.agents.docker_policy.verifier_exit_code", return_value=0) as scan:
                with self.assertRaises(exceptions.ValidationError):
                    verify_admission("sha256:" + "a" * 64)
                scan.assert_not_called()

    @override_settings(NEXUS_AGENT_IMAGE_ADMISSION_COMMAND=["scanner", "verify"])
    def test_admission_process_errors_do_not_expose_argv_or_output(self):
        import subprocess
        for failure in (OSError("private-operator-argument"),
                        subprocess.TimeoutExpired(["private-operator-argument"], 180), ValueError("private-operator-argument")):
            with self.subTest(kind=type(failure).__name__), \
                 patch("apps.agents.docker_policy.verifier_exit_code", side_effect=failure):
                with self.assertRaisesMessage(exceptions.ValidationError, "AGENT_IMAGE_ADMISSION_UNAVAILABLE") as error:
                    verify_admission("sha256:" + "a" * 64)
                self.assertNotIn("private-operator-argument", str(error.exception))
                self.assertTrue(error.exception.__suppress_context__)

    def test_remote_daemon_is_rejected_instead_of_returning_wrong_loopback_endpoint(self):
        with patch.dict("os.environ", {"DOCKER_HOST": "tcp://worker.invalid:2376"}), \
             self.assertRaisesMessage(exceptions.APIException, "AGENT_DOCKER_TOPOLOGY_UNSUPPORTED"):
            assert_host()

    def test_nonmatching_initialize_id_is_rejected(self):
        self.assertFalse(runner._valid_mcp_initialize(json.dumps({"jsonrpc": "2.0", "id": "wrong", "result": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "test", "version": "1"}}}).encode()))

    def test_legacy_restarted_port_is_not_mistaken_for_another_agents_health(self):
        deployment = SimpleNamespace(container_id="container", internal_mcp_url="http://127.0.0.1:12345/mcp")
        info = {"State": {"Running": True}, "NetworkSettings": {"Ports": {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "23456"}]}}}
        with patch.object(runner, "_docker_inspect", return_value=info), patch.object(runner, "_wait_for_mcp") as health:
            self.assertFalse(runner.DockerAgentRuntimeRunner().health_check(deployment=deployment))
        health.assert_not_called()

    @override_settings(NEXUS_AGENT_RUNTIME_CONTROLLER_CONTAINER="nexus-agent-runtime-controller")
    def test_controller_mode_uses_private_network_without_host_port(self):
        deployment = SimpleNamespace(
            id="controller-runtime", tenant_id="tenant", project_id=None, agent_id="agent",
            docker_lifecycle={"generation": "generation1"},
            image=SimpleNamespace(pk=None, version=None, image_ref="example/agent:v1", image_digest="sha256:" + "a" * 64),
            agent=SimpleNamespace(resource_config=None),
        )

        def inspect(kind, identifier):
            if kind == "image":
                return {"Id": "sha256:" + "a" * 64}
            if kind == "container" and identifier == "nexus-agent-runtime-controller":
                return {"Config": {"Labels": {"nexus.managed": "agent-controller"}}}
            return None

        with patch.object(runner, "_docker_inspect", side_effect=inspect), \
             patch.object(runner, "_run_docker", return_value="container-id") as docker, \
             patch.object(runner, "_wait_for_mcp", return_value=True):
            result = runner.DockerAgentRuntimeRunner().start(deployment=deployment)

        run_args = next(call.args[0] for call in docker.call_args_list if call.args[0][1] == "run")
        self.assertNotIn("-p", run_args)
        self.assertEqual(result.internal_mcp_url, "http://nexus-agent-controllerruntime-generation1:8000/mcp")
        self.assertTrue(any(call.args[0][1:3] == ["network", "connect"] for call in docker.call_args_list))

    @override_settings(NEXUS_AGENT_RUNTIME_ORPHAN_GRACE_SECONDS=300, NEXUS_AGENT_RUNTIME_HOST_ID="worker-1")
    def test_orphan_sweep_removes_only_old_owned_container(self):
        old = "2020-01-01T00:00:00Z"
        info = {
            "Id": "a" * 64,
            "Created": old,
            "Config": {"Labels": {
                "nexus.managed": "agent",
                "nexus.agent.runtime": "orphan-runtime",
                "nexus.agent.host": "worker-1",
            }},
            "NetworkSettings": {"Networks": {}},
        }

        def docker(command, **_kwargs):
            if command[1:3] == ["ps", "-a"]:
                return "a" * 64
            if command[1:3] == ["network", "ls"]:
                return ""
            return ""

        with patch.object(runner, "_run_docker", side_effect=docker) as command, \
             patch.object(runner, "_docker_inspect", return_value=info):
            result = runner._sweep_orphaned_agent_objects(valid_runtime_ids=set())

        self.assertEqual(result["containers"], 1)
        self.assertTrue(any(call.args[0][1] == "rm" for call in command.call_args_list))

    def test_docker_log_tail_redacts_common_credentials(self):
        completed = SimpleNamespace(
            stdout="Authorization: Bearer top-secret-token\npassword=hunter2\n",
            stderr="api_key=sk-example-secret-value",
        )
        with patch.object(runner.subprocess, "run", return_value=completed):
            value = runner._docker_log_tail("container")
        self.assertNotIn("top-secret-token", value)
        self.assertNotIn("hunter2", value)
        self.assertNotIn("sk-example", value)
        self.assertIn("[REDACTED]", value)

    @override_settings(NEXUS_AGENT_RUNTIME_HOST_ID="docker-test-isolated", NEXUS_AGENT_RUNTIME_ORPHAN_GRACE_SECONDS=0)
    def test_test_database_orphan_sweep_preserves_business_host_objects(self):
        labels = {"nexus.managed":"agent", "nexus.agent.runtime":"business-runtime", "nexus.agent.host":"local"}
        def inspect(kind, identifier):
            return {"Id":identifier, "Created":"2020-01-01T00:00:00Z", "Labels":labels,
                    "Config":{"Labels":labels}, "Containers":{}}
        def command(arguments, **kwargs):
            if arguments[1:3] == ["ps", "-a"]:
                return "business-container"
            if arguments[1:3] == ["network", "ls"]:
                return "business-network"
            raise AssertionError("Other host must not be stopped, disconnected or removed")
        with patch.object(runner, "_run_docker", side_effect=command) as calls, \
             patch.object(runner, "_docker_inspect", side_effect=inspect):
            result = runner._sweep_orphaned_agent_objects(valid_runtime_ids=set())
        self.assertEqual(result, {"containers":0, "networks":0})
        self.assertEqual(calls.call_count, 2)

    def test_missing_local_image_is_restored_from_shared_artifact(self):
        digest = "sha256:" + "a" * 64
        with tempfile.TemporaryDirectory() as root:
            artifact = Path(root) / "tenant" / "agent-image.tar"
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"docker-image")
            deployment = SimpleNamespace(
                image=SimpleNamespace(
                    pk="image-id",
                    image_ref="example/agent:v1",
                    image_digest=digest,
                    artifact_path="tenant/agent-image.tar",
                )
            )
            restored = False

            def inspect(kind, identifier):
                if kind == "image" and identifier == digest and restored:
                    return {"Id": digest}
                return None

            def load_image(*, artifact_path, image_ref):
                nonlocal restored
                restored = True
                self.assertEqual(Path(artifact_path), artifact)
                self.assertEqual(image_ref, "example/agent:v1")
                return image_ref

            docker_runner = runner.DockerAgentRuntimeRunner()
            with override_settings(NEXUS_AGENT_STORAGE_ROOT=root), \
                 patch.object(runner, "_docker_inspect", side_effect=inspect), \
                 patch.object(docker_runner, "load_image", side_effect=load_image) as load:
                docker_runner._ensure_runtime_image(deployment=deployment, image_id=digest)

            load.assert_called_once()
