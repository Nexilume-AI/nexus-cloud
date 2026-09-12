"""Original Agent file contracts and storage/HTTP assertions, shared across editions."""
import asyncio
import hashlib
import io
import json
import tempfile
from datetime import timedelta
from unittest import mock
from django.db import close_old_connections
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import exceptions
from apps.agents import file_transfers as files
from apps.agents.models import AgentDisplayRun, AgentFileTransfer, AgentFilePart
from apps.agents.file_views import FileStream
from apps.datasets.storage_backends import get_dataset_storage_backend
from tests.agent_task_arguments import queued_arguments


class FileArgumentGuards:
    @staticmethod
    def file_schema():
        return {"type": "object", "properties": {
            "message": {"type": "string"}, "files": {"type": "array", "maxItems": 8,
                "items": {"type": "object", "properties": {
                    "file_id": {"type": "string"}, "name": {"type": "string"},
                    "content_type": {"type": "string"}, "size_bytes": {"type": "integer"},
                    "sha256": {"type": "string"}},
                    "required": ["file_id"], "additionalProperties": False}}},
            "required": ["message"], "additionalProperties": False}

    def test_old_file_tool_accepts_cloud_metadata_without_mutating_snapshot(self):
        from apps.agents.runtime_services import _private_run_arguments
        row = AgentFileTransfer(name="sample.txt", content_type="text/plain", size_bytes=5,
            sha256="a" * 64, source_kind="computer", source_label="Attached Computer",
            expires_at=timezone.now() + timedelta(hours=1))
        references = files.file_arguments([row])
        for text in ("Read the file", ""):
            with self.subTest(text=text):
                candidate, _ = _private_run_arguments(descriptor={"input_schema": self.file_schema()},
                    content=text, arguments=None, file_arguments=references)
                self.assertEqual(set(candidate["files"][0]), {"file_id", "name", "content_type", "size_bytes", "sha256"})
                self.assertEqual(candidate["files"][0]["file_id"], str(row.pk))
        self.assertEqual(references[0]["source_kind"], "computer")
        self.assertEqual(references[0]["source_label"], "Attached Computer")

    def test_declared_source_metadata_is_preserved_and_invalid_fields_still_fail(self):
        from apps.agents.runtime_services import _private_run_arguments
        schema = self.file_schema()
        item = schema["properties"]["files"]["items"]
        item["properties"]["source_kind"] = {"type": "string", "enum": ["upload"]}
        refs = [{"file_id": "file", "source_kind": "upload", "source_label": "local"}]
        candidate, _ = _private_run_arguments(descriptor={"input_schema": schema}, content="Read", arguments=None, file_arguments=refs)
        self.assertEqual(candidate["files"], [{"file_id": "file", "source_kind": "upload"}])
        for invalid in ({"file_id": 42}, {"file_id": "file", "source_kind": "invalid"},
                        {"file_id": "file", "unexpected": "not silently removed"}):
            with self.subTest(invalid=invalid), self.assertRaises(exceptions.ValidationError):
                _private_run_arguments(descriptor={"input_schema": schema}, content="Read", arguments=None, file_arguments=[invalid])

    def test_audio_uses_its_own_contract_and_explicit_arguments_are_not_filtered(self):
        from apps.agents.runtime_services import _private_run_arguments
        schema = self.file_schema()
        schema["properties"]["audio"] = schema["properties"].pop("files")
        reference = {"file_id": "audio", "source_kind": "audio", "source_label": "Recording"}
        candidate, _ = _private_run_arguments(descriptor={"input_schema": schema}, content="Listen", arguments=None, audio_arguments=[reference])
        self.assertEqual(candidate["audio"], [{"file_id": "audio"}])
        with self.assertRaises(exceptions.ValidationError):
            _private_run_arguments(descriptor={"input_schema": schema}, content="",
                arguments={"message": "Listen", "audio": [reference]})

    def test_current_file_example_declares_provenance_and_errors_do_not_echo_values(self):
        import ast
        from pathlib import Path
        from apps.agents.runtime_services import _private_run_arguments
        example = Path(__file__).resolve().parents[2] / "nexus_openwrt/sdk/nexus-agent-sdk-python/examples/router_file_agent.py"
        tree = ast.parse(example.read_text(encoding="utf-8"))
        declaration = next(node.value for node in tree.body if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "FILE_TOOL" for target in node.targets))
        schema = ast.literal_eval(next(arg.value for arg in declaration.keywords if arg.arg == "input_schema"))
        reference = {"file_id": "file", "source_kind": "upload", "source_label": "Local file"}
        candidate, _ = _private_run_arguments(descriptor={"input_schema": schema}, content="Read", arguments=None, file_arguments=[reference])
        self.assertEqual(candidate["files"], [reference])
        with self.assertRaises(exceptions.ValidationError) as caught:
            _private_run_arguments(descriptor={"input_schema": schema}, content="Read", arguments=None,
                file_arguments=[{"file_id": {"secret": "never-echo-this-value"}}])
        self.assertIn('files.0.file_id', str(caught.exception.detail))
        self.assertNotIn("never-echo-this-value", str(caught.exception.detail))
        self.assertNotIn("structured arguments", str(caught.exception.detail))

    def test_attachment_only_input_is_governed_by_the_published_schema(self):
        from apps.agents.runtime_services import _private_run_arguments
        descriptor = {"input_schema": {"type": "object", "properties": {"attachments": {"type": "array"}}, "required": ["attachments"], "additionalProperties": False}}
        candidate, text = _private_run_arguments(descriptor=descriptor, content="", arguments=None, image_arguments=[{"asset_id": "image"}])
        self.assertEqual(candidate, {"attachments": [{"asset_id": "image"}]})
        self.assertEqual(text, "")
        with self.assertRaisesMessage(exceptions.ValidationError, "Run input must be 1..8000 characters"):
            _private_run_arguments(descriptor=descriptor, content="", arguments=None)
        descriptor["input_schema"]["properties"]["message"] = {"type": "string", "minLength": 1}
        descriptor["input_schema"]["required"].append("message")
        with self.assertRaises(exceptions.ValidationError):
            _private_run_arguments(descriptor=descriptor, content="", arguments=None, image_arguments=[{"asset_id": "image"}])


class AgentFileHelpers:
    def create(self, size, **extra):
        response = self.client.post("/api/v1/agent-files/", {"agent_id": str(self.agent.pk), "name": "large.bin", "size_bytes": size, **extra}, format="json", **self._headers(self.project_a))
        self.assertEqual(response.status_code, 201, response.content)
        return self.file_payload(response)

    def put(self, info, data, offset=0, client=None, headers=None):
        return (client or self.client).put(f"/api/v1/agent-files/{info['file_id']}/", data, content_type="application/octet-stream",
            HTTP_X_NEXUS_UPLOAD_OFFSET=str(offset), HTTP_X_NEXUS_CHUNK_SHA256=hashlib.sha256(data).hexdigest(), **(headers or self._headers(self.project_a)))

    def upload(self, size=2 * files.CHUNK_SIZE + 17):
        info = self.create(size)
        digest = hashlib.sha256()
        for offset in range(0, size, files.CHUNK_SIZE):
            data = bytes([offset // files.CHUNK_SIZE % 255]) * min(files.CHUNK_SIZE, size - offset)
            digest.update(data)
            response = self.put(info, data, offset)
            self.assertEqual(response.status_code, 200, response.content)
        complete = self.client.post(f"/api/v1/agent-files/{info['file_id']}/", {}, format="json", **self._headers(self.project_a))
        self.assertEqual(complete.status_code, 202, complete.content)
        self.assertTrue(files.process_one())
        row = AgentFileTransfer.objects.get(pk=info["file_id"])
        self.assertEqual(row.state, "ready", row.error_code)
        self.assertEqual(row.sha256, digest.hexdigest())
        return row

    def start(self, row):
        response = self.client.post(f"/api/v1/agents/{self.agent.pk}/private-runs/", {"content": "Inspect file", "files": [str(row.pk)]}, format="json", **self._headers(self.project_a))
        self.assertEqual(response.status_code, 202, response.content)
        return AgentDisplayRun.objects.get(pk=self.file_payload(response)["run_id"])


class AgentFileHTTPGuards:
    def test_chunks_idempotency_offsets_size_hash_and_cancel(self):
        info = self.create(files.CHUNK_SIZE + 1)
        data = b"a" * files.CHUNK_SIZE
        self.assertEqual(self.put(info, data).status_code, 200)
        self.assertEqual(self.put(info, data).status_code, 200)
        self.assertEqual(self.put(info, b"b" * files.CHUNK_SIZE).status_code, 409)
        self.assertEqual(self.put(info, b"a", 2 * files.CHUNK_SIZE).status_code, 409)
        path = f"/api/v1/agent-files/{info['file_id']}/"
        self.assertEqual(self.client.post(path, {}, format="json", **self._headers(self.project_a)).status_code, 409)
        self.assertEqual(self.client.put(path, b"x", content_type="application/octet-stream", HTTP_X_NEXUS_UPLOAD_OFFSET=str(files.CHUNK_SIZE), HTTP_X_NEXUS_CHUNK_SHA256="wrong", **self._headers(self.project_a)).status_code, 400)
        self.assertEqual(self.client.delete(path, **self._headers(self.project_a)).status_code, 204)
        files.cleanup()
        self.assertEqual(AgentFileTransfer.objects.get(pk=info["file_id"]).state, "purged")
        self.assertFalse(AgentFilePart.objects.exists())

    def test_private_binding_download_ranges_and_other_caller_denied(self):
        row = self.upload()
        run = self.start(row)
        row.refresh_from_db()
        self.assertEqual(row.run_id, run.pk)
        message = run.messages.get(role="user")
        blocks = [block for block in message.content_blocks if block["type"] == "file"]
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["name"], row.name)
        self.assertEqual(blocks[0]["url"], f"/api/v1/agent-runs/{run.pk}/files/{row.pk}/download/")
        args = json.loads(queued_arguments(run.execution_task)["body"])["params"]["arguments"]
        self.assertEqual(args["files"][0]["file_id"], str(row.pk))
        self.assertLess(len(json.dumps(args)), 1024)
        headers = self._display_headers(str(run.pk))
        path = f"/api/v1/agent-runs/{run.pk}/files/{row.pk}/download/"
        response = self.client.get(path, HTTP_RANGE=f"bytes={files.CHUNK_SIZE}-", **headers)
        self.assertEqual(response.status_code, 206, response.content if not response.streaming else "")
        self.assertEqual(b"".join(response.streaming_content), b"\x01" * files.CHUNK_SIZE + b"\x02" * 17)
        self.assertEqual(self.client.head(path, **headers)["Content-Length"], str(row.size_bytes))
        self.assertEqual(self.client.get(path, HTTP_RANGE="bytes=999999999-", **headers).status_code, 416)
        self.assertEqual(self.client.get(f"/api/v1/agent-files/{row.pk}/download/", **self._headers(self.project_a)).status_code, 404)
        self.assert_other_file_caller_denied(row, path, headers)
        response = self.client.post(f"/api/v1/agents/{self.agent.pk}/private-runs/", {"content": "reuse", "files": [str(row.pk)]}, format="json", **self._headers(self.project_a))
        self.assertEqual(response.status_code, 404)

    def test_native_download_cookie_requires_initial_identity_and_token(self):
        row = self.upload(3)
        run = self.start(row)
        path = f"/api/v1/agent-runs/{run.pk}/files/{row.pk}/download/"
        self.assertEqual(self.client.post(path, {}, format="json", **self._headers(self.project_a)).status_code, 404)
        prepared = self.client.post(path, {}, format="json", **self._display_headers(str(run.pk)))
        self.assertEqual(prepared.status_code, 200, prepared.content)
        self.assertEqual(self.file_payload(prepared)["url"], path)
        browser = APIClient()
        browser.cookies.update(prepared.cookies)
        response = browser.get(path, HTTP_RANGE="bytes=1-")
        self.assertEqual(response.status_code, 206, response.content if not response.streaming else "")
        self.assertEqual(b"".join(response.streaming_content), b"\x00\x00")
        run.caller_hidden_at = timezone.now(); run.save()
        self.assertEqual(browser.get(path).status_code, 404)

    def test_internal_output_snapshot_without_computer_and_range_download(self):
        run = self.start(self.upload(1))
        context = queued_arguments(run.execution_task)["display_pair"][1]
        client = APIClient()
        headers = {"HTTP_X_NEXUS_INTERACTION_TOKEN": context.interaction_token}
        path = f"/api/v1/internal/agent-runs/{run.pk}/files/"
        create_payload = {
            "name": "result.zip",
            "size_bytes": 7,
            "sha256": hashlib.sha256(b"payload").hexdigest(),
            "idempotency_key": "nxo_run_file_create",
        }
        created = client.post(path, create_payload, format="json", **headers)
        self.assertEqual(created.status_code, 201, created.content)
        replayed = client.post(path, create_payload, format="json", **headers)
        self.assertEqual(replayed.status_code, 201, replayed.content)
        self.assertEqual(self.file_payload(replayed)["file_id"], self.file_payload(created)["file_id"])
        self.assertEqual(run.file_transfers.filter(direction="output").count(), 1)
        item = path + self.file_payload(created)["file_id"] + "/"
        response = client.put(item, b"payload", content_type="application/octet-stream", HTTP_X_NEXUS_UPLOAD_OFFSET="0", HTTP_X_NEXUS_CHUNK_SHA256=hashlib.sha256(b"payload").hexdigest(), **headers)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(client.post(item, {}, format="json", **headers).status_code, 202)
        files.process_one()
        artifact = run.output_artifacts.get()
        self.assertEqual(artifact.snapshot_status, "ready")
        self.assertEqual(artifact.sha256, hashlib.sha256(b"payload").hexdigest())
        response = self.client.get(f"/api/v1/agent-runs/{run.pk}/outputs/{artifact.pk}/download/", HTTP_RANGE="bytes=2-4", **self._display_headers(str(run.pk)))
        self.assertEqual(response.status_code, 206, response.content if not response.streaming else "")
        self.assertEqual(b"".join(response.streaming_content), b"ylo")
        self.assertEqual(client.get(item, HTTP_X_NEXUS_INTERACTION_TOKEN="other-run").status_code, 404)
        run.status = "completed"; run.save()
        self.assertEqual(client.get(item, **headers).status_code, 404)

    def test_integrity_failure_retry_quota_and_cleanup_do_not_starve(self):
        row = self.upload(3)
        files.cleanup()  # only immutable file remains; must not starve cancellation
        info = self.create(1, sha256="0" * 64)
        self.put(info, b"x")
        target = AgentFileTransfer.objects.get(pk=info["file_id"])
        files.complete_transfer(target); files.process_one(); target.refresh_from_db()
        self.assertEqual(target.state, "failed")
        self.assertIsNone(target.artifact_id)
        files.cleanup()
        self.assertTrue(target.parts.filter(index=0).exists())
        files.cancel_transfer(target)
        files.cleanup(limit=1)
        target.refresh_from_db(); self.assertEqual(target.state, "purged")
        with override_settings(NEXUS_AGENT_FILE_TENANT_BYTES=3):
            response = self.client.post("/api/v1/agent-files/", {"agent_id": str(self.agent.pk), "name": "over", "size_bytes": 1}, format="json", **self._headers(self.project_a))
            self.assertEqual(response.status_code, 400)
        self.assertTrue(get_dataset_storage_backend().exists(object_key=row.object_key))

    def test_expired_and_cross_project_uploads_and_undeclared_tools_rejected(self):
        row = self.upload(1)
        self.assert_other_file_project_denied(row)
        self.agent.repo_metadata = {}; self.agent.save()
        response = self.client.post(f"/api/v1/agents/{self.agent.pk}/private-runs/", {"content": "invalid", "files": [str(row.pk)]}, format="json", **self._headers(self.project_a))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(AgentDisplayRun.objects.exists())
        row.expires_at = timezone.now() - timedelta(seconds=1); row.save()
        files.cleanup(); row.refresh_from_db()
        self.assertEqual(row.state, "purged")

    def test_64_mib_round_trip_uses_bounded_reads(self):
        row = self.upload(64 * 1024**2 + 17)
        store = get_dataset_storage_backend()
        digest = hashlib.sha256(); count = 0
        for chunk in FileStream(store.open(object_key=row.object_key), row.size_bytes):
            self.assertLessEqual(len(chunk), files.STREAM_CHUNK)
            digest.update(chunk); count += len(chunk)
        self.assertEqual(count, row.size_bytes)
        self.assertEqual(digest.hexdigest(), row.sha256)

    def test_async_stream_never_uses_unbounded_read(self):
        class Guarded(io.BytesIO):
            def read(self, size=-1):
                assert 0 <= size <= files.STREAM_CHUNK
                return super().read(size)
        async def consume():
            stream = FileStream(Guarded(b"a" * (files.STREAM_CHUNK + 9)), files.STREAM_CHUNK + 9)
            return sum([len(chunk) async for chunk in stream.aiter()])
        self.assertEqual(asyncio.run(consume()), files.STREAM_CHUNK + 9)

    def test_mcp_task_binds_uploaded_file_and_replaces_untrusted_metadata(self):
        row = self.upload(5)
        response = self.client.post(f"/api/v1/agents/{self.agent.pk}/mcp/", {"jsonrpc": "2.0", "id": "file-call", "method": "tools/call",
            "params": {"name": "inspect", "arguments": {"message": "file", "files": [{"file_id": str(row.pk), "name": "forged", "url": "https://invalid.example"}]},
                "_meta": {"io.modelcontextprotocol/clientCapabilities": {"extensions": {"io.modelcontextprotocol/tasks": {}}}}}},
            format="json", HTTP_MCP_PROTOCOL_VERSION="2026-07-28", **self._headers(self.project_a))
        self.assertEqual(response.status_code, 200, response.content)
        row.refresh_from_db()
        self.assertIsNotNone(row.run_id)
        arguments = json.loads(queued_arguments(row.run.execution_task)["body"])["params"]["arguments"]
        self.assertEqual(arguments["files"][0]["name"], "large.bin")
        self.assertNotIn("url", arguments["files"][0])
        self.assertNotIn("source_kind", arguments["files"][0])
        self.assertNotIn("source_label", arguments["files"][0])

    def test_finalization_storage_error_can_retry_without_duplicate_artifact(self):
        info = self.create(1)
        self.put(info, b"x")
        row = AgentFileTransfer.objects.get(pk=info["file_id"])
        files.complete_transfer(row)
        with mock.patch("apps.datasets.storage_backends.LocalDatasetStorageBackend.save", side_effect=OSError("private-backend-credential")):
            files.process_one()
        row.refresh_from_db()
        self.assertEqual(row.state, "failed")
        self.assertNotIn("credential", str(files.metadata(row)))
        files.complete_transfer(row); files.process_one(); row.refresh_from_db()
        self.assertEqual(row.state, "ready")
        self.assertFalse(files.process_one())
        self.assertEqual(row.attempts, 2)

    def test_reserved_chunk_key_survives_io_failure_and_cancel_cleans_it(self):
        info = self.create(1)
        row = AgentFileTransfer.objects.get(pk=info["file_id"])
        with mock.patch("apps.datasets.storage_backends.LocalDatasetStorageBackend.save", side_effect=OSError("interrupted")):
            with self.assertRaises(files.FileUnavailable):
                files.put_part(row, offset=0, body=b"x", digest=hashlib.sha256(b"x").hexdigest())
        self.assertEqual(row.parts.count(), 1)
        row.refresh_from_db(); self.assertEqual(row.received_bytes, 0)
        files.put_part(row, offset=0, body=b"x", digest=hashlib.sha256(b"x").hexdigest())
        row.refresh_from_db(); self.assertEqual(row.received_bytes, 1)
        files.cancel_transfer(row); files.cleanup()
        row.refresh_from_db(); self.assertEqual(row.state, "purged")


class AgentFileCommittedGuards:
    def test_concurrent_identical_chunks_commit_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        info = self.create(files.CHUNK_SIZE)
        row = AgentFileTransfer.objects.get(pk=info["file_id"])
        barrier = Barrier(2)
        def upload():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return files.put_part(row, offset=0, body=b"a" * files.CHUNK_SIZE, digest=hashlib.sha256(b"a" * files.CHUNK_SIZE).hexdigest()).received_bytes
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(list(executor.map(lambda _: upload(), range(2))), [files.CHUNK_SIZE] * 2)
        self.assertEqual(row.parts.count(), 1)

    @override_settings(ALLOWED_HOSTS=["127.0.0.1", "testserver"])
    def test_real_http_sdk_roundtrip_with_background_finalizer(self):
        """Real urllib SDK + HTTP Django endpoint + committed DB + storage worker."""
        import sys
        import threading
        from pathlib import Path
        from wsgiref.simple_server import make_server, WSGIRequestHandler
        from django.core.wsgi import get_wsgi_application
        sdk = Path(__file__).resolve().parents[2] / "nexus_openwrt/sdk/nexus-agent-sdk-python/src"
        sys.path.insert(0, str(sdk))
        self.addCleanup(lambda: sys.path.remove(str(sdk)))
        from nexus_agent import NexusRunContext
        row = self.upload(8 * 1024**2 + 17)
        run = self.start(row)
        context = queued_arguments(run.execution_task)["display_pair"][1]
        class QuietHandler(WSGIRequestHandler):
            def log_message(self, *args):
                pass
        server = make_server("127.0.0.1", 0, get_wsgi_application(), handler_class=QuietHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        stop = threading.Event()
        failures = []
        def worker():
            try:
                while not stop.wait(0.05):
                    close_old_connections()
                    files.process_one()
            except Exception as exc:
                failures.append(type(exc).__name__)
            finally:
                close_old_connections()
        worker_thread = threading.Thread(target=worker, daemon=True)
        worker_thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                ctx = NexusRunContext(run_id=str(run.pk), events_url="", token="", interaction_token=context.interaction_token,
                    display_asset_url=f"http://127.0.0.1:{server.server_port}/api/v1/internal/agent-runs/{run.pk}/display-assets/")
                try:
                    downloaded = ctx.files.download({"file_id": str(row.pk)}, Path(folder) / "roundtrip.bin")
                    result = ctx.output.upload_file(downloaded, timeout=20)
                    self.assertEqual(result["sha256"], row.sha256)
                    self.assertEqual(result["size_bytes"], row.size_bytes)
                    self.assertTrue(result["artifact_id"])
                    self.assertEqual(run.output_artifacts.count(), 1)
                    self.assertEqual(failures, [])
                finally:
                    ctx.close()
        finally:
            stop.set(); worker_thread.join(timeout=5)
            server.shutdown(); server.server_close(); server_thread.join(timeout=5)
