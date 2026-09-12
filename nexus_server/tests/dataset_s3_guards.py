"""Unmodified real 128 MiB S3 integration assertions shared by both hosts."""
import hashlib
import tracemalloc
import uuid
from types import SimpleNamespace

from django.test import override_settings

from apps.datasets.storage_backends import S3CompatibleDatasetStorageBackend
from tests.dataset_streaming_guards import GeneratedUpload


class DatasetS3Guards:
    def test_real_128_mib_multipart_roundtrip_and_range(self):
        endpoint = self.s3_endpoint()
        self.assertTrue(endpoint.startswith("http://127.0.0.1:"), "Only a disposable localhost test store is permitted")
        bucket = "nexus-transfer-qa-" + uuid.uuid4().hex
        with override_settings(NEXUS_DATASET_S3_BUCKET=bucket, NEXUS_DATASET_S3_ENDPOINT_URL=endpoint,
            NEXUS_DATASET_S3_ACCESS_KEY_ID="nexusqa", NEXUS_DATASET_S3_SECRET_ACCESS_KEY="nexus_transfer_test_only",
            NEXUS_DATASET_S3_REGION="us-east-1"):
            backend = S3CompatibleDatasetStorageBackend()
            client = backend.client()
            client.create_bucket(Bucket=bucket)
            stored = None
            try:
                tracemalloc.start()
                stored = backend.save(tenant=SimpleNamespace(id="qa"), dataset_id="large",
                    file_name="128m.bin", uploaded_file=GeneratedUpload(128 * 1024 * 1024))
                digest, read_bytes = hashlib.sha256(), 0
                with backend.open(object_key=stored.object_key) as stream:
                    while chunk := stream.read(256 * 1024):
                        digest.update(chunk)
                        read_bytes += len(chunk)
                _, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                self.assertEqual(read_bytes, stored.size_bytes)
                self.assertEqual(digest.hexdigest(), stored.sha256)
                self.assertLess(peak, 48 * 1024 * 1024)
                with backend.open_range(object_key=stored.object_key, start=17 * 1024 * 1024 + 3, length=37) as stream:
                    self.assertEqual(stream.read(), b"x" * 37)
                self.assertEqual(client.list_multipart_uploads(Bucket=bucket).get("Uploads", []), [])
                print(f"\nMinIO roundtrip: {read_bytes} bytes; Python traced peak: {peak} bytes")
            finally:
                if tracemalloc.is_tracing():
                    tracemalloc.stop()
                if stored:
                    backend.delete(object_key=stored.object_key)
                client.delete_bucket(Bucket=bucket)
