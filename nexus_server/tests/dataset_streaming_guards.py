"""Portable streaming unit assertions; S3 transport here is explicitly mocked."""
import asyncio
import hashlib
import io
import tempfile
import tracemalloc
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.test import AsyncRequestFactory, override_settings
from apps.datasets.downloads import DownloadStream, selected_range, file_response
from apps.datasets.storage_backends import LocalDatasetStorageBackend, S3CompatibleDatasetStorageBackend


class GeneratedUpload:
    name = "large.bin"
    content_type = "application/octet-stream"

    def __init__(self, size):
        self.size = size

    def chunks(self):
        remaining = self.size
        while remaining:
            chunk = b"x" * min(256 * 1024, remaining)
            remaining -= len(chunk)
            yield chunk


class DatasetStreamingGuards:
    def test_response_uses_native_async_iterator_under_asgi(self):
        request = AsyncRequestFactory().get("/api/v1/download/")
        stream = io.BytesIO(b"x" * (2 * 1024 * 1024))
        backend = SimpleNamespace(open=lambda **kwargs: stream)
        with mock.patch("apps.datasets.downloads.get_dataset_storage_backend", return_value=backend), \
             mock.patch("apps.datasets.downloads.reserve_export", return_value=SimpleNamespace(size_bytes=2 * 1024 * 1024)), \
             mock.patch("apps.datasets.services.log_write"):
            response = file_response(request=request, dataset=SimpleNamespace(), object_key="key", storage_backend="local",
                file_name="large.bin", size=2 * 1024 * 1024, sha256="abc", content_type="application/octet-stream", action="test", file_id="file")
        self.assertTrue(response.is_async)
        async def consume():
            chunk = await anext(response._iterator)
            self.assertEqual(stream.tell(), len(chunk))
            await response._iterator.aclose()
        asyncio.run(consume())
        self.assertTrue(stream.closed)

    def test_streaming_scan_detects_secrets_across_chunk_boundaries(self):
        from apps.agents.output_scanning import scan_output_stream, CHUNK
        content = b"ordinary prose. " * (CHUNK // 16 - 1) + b"prefix Bearer " + b"a" * 40
        result = scan_output_stream(io.BytesIO(content))
        self.assertEqual(result["finding_count"], 1)
        self.assertEqual(result["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(scan_output_stream(io.BytesIO(b"\xff\x00"))["error_code"], "BINARY_SCANNER_REQUIRED")
        self.assertEqual(scan_output_stream(io.BytesIO(b"x" * 8192))["error_code"], "SCAN_LEXICAL_SPAN_TOO_LONG")

    def test_range_validation(self):
        for value, expected in [("bytes=2-4", (2, 3)), ("bytes=-3", (7, 3)), ("bytes=2-", (2, 8)),
                                ("bytes=10-", None), ("bytes=0-1,4-5", None), ("bytes=-0", None)]:
            self.assertEqual(selected_range(value, 10), expected)

    def test_asgi_first_chunk_does_not_buffer_file_and_close_on_cancel(self):
        stream = io.BytesIO(b"x" * (2 * 1024 * 1024))
        body = DownloadStream(stream, 2 * 1024 * 1024, SimpleNamespace(size_bytes=2 * 1024 * 1024))
        async def consume():
            iterator = body.aiter()
            chunk = await anext(iterator)
            self.assertEqual(len(chunk), 256 * 1024)
            self.assertEqual(stream.tell(), len(chunk))
            await iterator.aclose()
        asyncio.run(consume())
        self.assertTrue(stream.closed)

    def test_truncated_stream_never_completes_export(self):
        body = DownloadStream(io.BytesIO(b"abc"), 10, SimpleNamespace(size_bytes=10))
        with mock.patch("apps.datasets.downloads.complete_export") as complete:
            with self.assertRaises(OSError):
                list(body)
            complete.assert_not_called()

    def test_local_save_failure_removes_partial_file(self):
        class Broken(GeneratedUpload):
            def chunks(self):
                yield b"first"
                raise OSError("source disconnected")
        with tempfile.TemporaryDirectory() as root, override_settings(NEXUS_DATASET_STORAGE_ROOT=root):
            with self.assertRaises(OSError):
                LocalDatasetStorageBackend().save(tenant=SimpleNamespace(id="tenant"), dataset_id="data",
                    file_name="broken.bin", uploaded_file=Broken(10))
            self.assertFalse([p for p in Path(root).rglob("*") if p.is_file()])

    @override_settings(NEXUS_DATASET_S3_BUCKET="qa")
    def test_128_mib_multipart_has_bounded_memory_and_aborts_on_failure(self):
        sizes = []
        class Client:
            def create_multipart_upload(self, **kwargs):
                return {"UploadId": "qa"}
            def upload_part(self, **kwargs):
                sizes.append(len(kwargs["Body"]))
                return {"ETag": str(kwargs["PartNumber"])}
            def complete_multipart_upload(self, **kwargs):
                self.completed = True
            def abort_multipart_upload(self, **kwargs):
                self.aborted = True
        client = Client()
        backend = S3CompatibleDatasetStorageBackend()
        with mock.patch.object(backend, "client", return_value=client):
            tracemalloc.start()
            stored = backend.save(tenant=SimpleNamespace(id="t"), dataset_id="d", file_name="large.bin",
                uploaded_file=GeneratedUpload(128 * 1024 * 1024))
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            self.assertEqual(stored.size_bytes, 128 * 1024 * 1024)
            self.assertEqual(len(sizes), 16)
            self.assertLess(peak, 32 * 1024 * 1024)
            with mock.patch.object(client, "upload_part", side_effect=OSError("unavailable")):
                with self.assertRaises(OSError):
                    backend.save(tenant=SimpleNamespace(id="t"), dataset_id="d", file_name="large.bin",
                        uploaded_file=GeneratedUpload(16 * 1024 * 1024))
            self.assertTrue(client.aborted)
