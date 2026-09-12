from __future__ import annotations

import socket
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.agents.runtime_controller import (
    AgentRuntimeControllerClient,
    AgentRuntimeControllerError,
    AgentRuntimeControllerServer,
    ControllerAgentRuntimeRunner,
    ensure_agent_runtime_controller_dependencies,
)
from apps.agents.runtime_runner import RuntimeMCPResult, RuntimeMCPStreamResult


class AgentRuntimeControllerProtocolTests(SimpleTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        if hasattr(socket, "AF_UNIX"):
            self.socket_path = str(Path(self.tempdir.name) / "agent.sock")
        else:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", 0))
                self.socket_path = f"tcp://127.0.0.1:{listener.getsockname()[1]}"
        self.server = None
        self.thread = None

    def tearDown(self) -> None:
        if self.server is not None:
            self.server.stop()
        if self.thread is not None:
            self.thread.join(timeout=3)

    def _start(self, dispatcher, stream_dispatcher=None):
        self.server = AgentRuntimeControllerServer(
            socket_path=self.socket_path,
            token="controller-secret",
            dispatcher=dispatcher,
            stream_dispatcher=stream_dispatcher,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                AgentRuntimeControllerClient(
                    socket_path=self.socket_path, token="controller-secret"
                ).ping()
                return
            except AgentRuntimeControllerError:
                time.sleep(0.01)
        self.fail("Agent Runtime Controller did not become ready")

    def test_authenticated_management_and_mcp_calls_use_controller(self) -> None:
        calls = []

        def dispatch(request):
            calls.append(request)
            if request["action"] == "ping":
                return {"status": "ready", "protocol_version": 1}
            if request["action"] == "start":
                return {"container_id": "container-1", "internal_mcp_url": "http://runtime:8000/mcp"}
            if request["action"] == "call_mcp":
                return {"status_code": 200, "headers": {"Content-Type": "application/json"}, "body": "e30="}
            return {}

        self._start(dispatch)
        client = AgentRuntimeControllerClient(socket_path=self.socket_path, token="controller-secret")
        runner = ControllerAgentRuntimeRunner(client=client)
        deployment = SimpleNamespace(id="runtime-1", container_id="container-1")
        started = runner.start(deployment=deployment)
        response = runner.call_mcp(
            deployment=deployment,
            method="POST",
            headers={"Content-Type": "application/json"},
            body=b"{}",
        )

        self.assertEqual(started.container_id, "container-1")
        self.assertEqual(response.body, b"{}")
        self.assertEqual(calls[-1]["runtime_id"], "runtime-1")
        self.assertNotIn("docker", " ".join(str(value) for value in calls[-1].values()).lower())

    def test_stream_frames_preserve_chunk_boundaries_and_close(self) -> None:
        def dispatch(request):
            return {"status": "ready", "protocol_version": 1}

        def stream(_request):
            return RuntimeMCPStreamResult(
                status_code=200,
                headers={"Content-Type": "text/event-stream"},
                chunks=iter([b"data: one\n\n", b"data: two\n\n"]),
            )

        self._start(dispatch, stream)
        runner = ControllerAgentRuntimeRunner(
            client=AgentRuntimeControllerClient(socket_path=self.socket_path, token="controller-secret")
        )
        result = runner.stream_mcp(
            deployment=SimpleNamespace(id="runtime-1"), method="POST", headers={}, body=b"{}"
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(b"".join(result.chunks), b"data: one\n\ndata: two\n\n")

    def test_wrong_controller_token_is_rejected(self) -> None:
        self._start(lambda request: {"status": "ready"})
        with self.assertRaisesRegex(AgentRuntimeControllerError, "authentication failed"):
            AgentRuntimeControllerClient(socket_path=self.socket_path, token="wrong").ping()

    @override_settings(NEXUS_AGENT_RUNTIME_CONTROLLER_MAX_FRAME_BYTES=256)
    def test_oversized_request_is_rejected_before_connect(self) -> None:
        client = AgentRuntimeControllerClient(socket_path=self.socket_path, token="controller-secret")
        with self.assertRaisesRegex(AgentRuntimeControllerError, "too large"):
            client.request(action="call_mcp", payload={"body": "x" * 512})

    @patch("apps.agents.runtime_controller.subprocess.run")
    @patch("apps.agents.runtime_controller.shutil.which", return_value="/usr/bin/docker")
    def test_dependency_check_requires_live_docker_engine(self, _which, run) -> None:
        run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="denied")
        with self.assertRaisesRegex(AgentRuntimeControllerError, "Docker Engine is unavailable"):
            ensure_agent_runtime_controller_dependencies()

    @override_settings(NEXUS_PRODUCTION=True, NEXUS_AGENT_IMAGE_ADMISSION_COMMAND=[])
    @patch("apps.agents.runtime_controller.subprocess.run")
    @patch("apps.agents.runtime_controller.shutil.which", return_value="/usr/bin/docker")
    def test_production_dependency_check_requires_admission_verifier(self, _which, run) -> None:
        run.return_value = SimpleNamespace(returncode=0, stdout="26.1", stderr="")
        with self.assertRaisesRegex(AgentRuntimeControllerError, "admission verification"):
            ensure_agent_runtime_controller_dependencies()


class AgentRuntimeControllerResultTests(SimpleTestCase):
    def test_call_result_is_losslessly_encoded(self) -> None:
        result = RuntimeMCPResult(status_code=201, headers={"X-Test": "yes"}, body=b"\x00\xff")
        self.assertEqual(result.body, b"\x00\xff")
