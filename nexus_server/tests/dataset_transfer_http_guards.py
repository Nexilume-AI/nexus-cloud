"""Common transfer HTTP guards; authentication and usage are edition fixtures."""
import io
import time
import uuid
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.agents.models import Agent, AgentOutputArtifact
from apps.datasets.agent_asset_services import StreamUpload, capture_agent_artifact_to_dataset
from apps.datasets.downloads import cookie_name
from apps.datasets.models import DatasetTransfer
from apps.datasets.services import create_dataset_file_from_upload
from apps.datasets.transfers import reserve_write, cleanup_expired_writes


class DatasetTransferHTTPGuards:
    def test_range_retry_and_head_count_one_logical_export(self):
        download_id = str(uuid.uuid4())
        headers = {**self.headers, "HTTP_X_NEXUS_DOWNLOAD_ID": download_id}
        head = self.client.head(self.path, **headers)
        self.assertEqual(head.status_code, 200)
        self.assertFalse(self.export_usage().exists())
        for selected, expected in [("bytes=2-4", b"234"), ("bytes=-3", b"789")]:
            response = self.client.get(self.path, HTTP_RANGE=selected, **headers)
            self.assertEqual(response.status_code, 206)
            self.assertEqual(b"".join(response.streaming_content), expected)
        self.assertEqual(self.export_usage().count(), 1)
        invalid = self.client.get(self.path, HTTP_RANGE="bytes=99-", **headers)
        self.assertEqual(invalid.status_code, 416)
        cached = self.client.get(self.path, HTTP_IF_NONE_MATCH=head["ETag"], **headers)
        self.assertEqual(cached.status_code, 304)
        full = self.client.get(self.path, HTTP_RANGE="bytes=2-4", HTTP_IF_RANGE='"changed"', **headers)
        self.assertEqual(full.status_code, 200)
        self.assertEqual(b"".join(full.streaming_content), b"0123456789")

    def test_native_cookie_no_url_secret_and_permission_rechecked(self):
        prepare = self.client.post(self.path, {}, format="json", **self.headers)
        self.assertEqual(prepare.status_code, 200, prepare.content)
        self.assertEqual(self.transfer_payload(prepare)["url"], self.path)
        cookie = prepare.cookies[cookie_name(self.path)]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Strict")
        native = APIClient()
        native.cookies[cookie.key] = cookie.value
        response = native.get(self.path)
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        self.assertEqual(b"".join(response.streaming_content), b"0123456789")
        # A new authenticated principal cannot use the old browser capability.
        self.authenticate_transfer_client(native, self.other)
        self.assertNotEqual(native.get(self.path, **self.headers).status_code, 200)
        self.authenticate_transfer_client(native, None)
        native.cookies[cookie.key] = cookie.value
        self.revoke_transfer_access()
        self.assertNotEqual(native.get(self.path).status_code, 200)

    def test_forged_cookie_and_anonymous_download_rejected(self):
        client = APIClient()
        self.assertNotEqual(client.get(self.path).status_code, 200)
        client.cookies[cookie_name(self.path)] = "forged"
        self.assertEqual(client.get(self.path).status_code, 404)

    def test_invalid_bearer_preserves_401_refresh_semantics(self):
        client = APIClient()
        response = client.post(self.path, {}, format="json", HTTP_AUTHORIZATION="Bearer invalid", **self.headers)
        self.assertEqual(response.status_code, 401)

    def test_cookie_expiry_and_path_binding(self):
        from apps.datasets.downloads import SALT
        prepared = self.client.post(self.path, {}, format="json", **self.headers)
        cookie = prepared.cookies[cookie_name(self.path)]
        native = APIClient()
        payload = signing.loads(cookie.value, salt=SALT)
        with mock.patch("django.core.signing.time.time", return_value=time.time() - 4000):
            native.cookies[cookie.key] = signing.dumps(payload, salt=SALT)
        self.assertEqual(native.get(self.path).status_code, 404)
        payload["path"] = "/a/different/file/"
        native.cookies[cookie.key] = signing.dumps(payload, salt=SALT)
        self.assertEqual(native.get(self.path).status_code, 404)

    def test_database_failure_cleans_object_and_does_not_increment_counter(self):
        with mock.patch("apps.datasets.services.DatasetFile.objects.create", side_effect=RuntimeError("database failed")):
            with self.assertRaises(RuntimeError):
                self.save(b"broken")
        self.dataset.refresh_from_db()
        self.assertEqual(self.dataset.file_count, 1)
        self.assertEqual(self.dataset.size_bytes, 10)
        self.assertEqual(len([p for p in Path(self.temp.name).rglob("*") if p.is_file()]), 1)

    def test_size_and_hash_mismatch_reject_without_publishing(self):
        for upload, sha in [(StreamUpload(io.BytesIO(b"extra"), name="x", size=2, content_type="text/plain"), None),
                            (SimpleUploadedFile("x", b"bad"), "0" * 64)]:
            with self.assertRaises(ValidationError):
                create_dataset_file_from_upload(dataset=self.dataset, uploaded_file=upload, uploaded_by=self.user, expected_sha256=sha)
        self.assertEqual(self.dataset.files.count(), 1)

    def test_invocation_artifact_captures_binary_snapshot_without_workspace(self):
        self.file = self.save(b"\x00\xff\xfe\x80binary")
        agent = Agent.objects.create(tenant=self.tenant, name="Agent", created_by=self.user)
        run = self.artifact_run_fixture(agent)
        artifact = AgentOutputArtifact.objects.create(tenant=self.tenant, agent=agent, run=run,
            workspace_path="private/data.bin", original_file_name="data.bin", snapshot_status="ready",
            snapshot_object_key=self.file.object_key, snapshot_storage_backend="local", size_bytes=self.file.size_bytes,
            sha256=self.file.sha256, scan_status="passed", policy_status="approved", license_status="internal")
        request = self.artifact_request()
        with mock.patch("apps.datasets.agent_asset_services.resolve_export_context", return_value=(self.dataset, agent)), \
             mock.patch("apps.datasets.agent_asset_services.read_workspace_file") as workspace, \
             mock.patch("apps.datasets.agent_asset_services.log_write"):
            saved = capture_agent_artifact_to_dataset(request=request, dataset_id=str(self.dataset.id), agent_id=str(agent.id), artifact_id=str(artifact.id))
        self.assertEqual(saved.sha256, self.file.sha256)
        self.assertNotEqual(saved.object_key, self.file.object_key)
        workspace.assert_not_called()

    def test_snapshot_scan_does_not_bless_changed_object_hash(self):
        from apps.agents.services import scan_output_artifact
        agent = Agent.objects.create(tenant=self.tenant, name="Scan Agent", created_by=self.user)
        run = self.artifact_run_fixture(agent)
        artifact = AgentOutputArtifact.objects.create(tenant=self.tenant, agent=agent, run=run,
            workspace_path="data.txt", original_file_name="data.txt", snapshot_status="ready",
            snapshot_object_key=self.file.object_key, snapshot_storage_backend="local", size_bytes=self.file.size_bytes,
            sha256="0" * 64, content_type="text/plain", license_status="internal")
        with mock.patch("apps.agents.services.get_mutable_agent", return_value=agent), mock.patch("apps.agents.services.log_write"):
            scanned = scan_output_artifact(request=self.artifact_request(), agent_id=str(agent.id),
                run_id=str(run.id), artifact_id=str(artifact.id))
        self.assertEqual(scanned.scan_status, "failed")
        self.assertEqual(scanned.scan_metadata["error_code"], "SNAPSHOT_INTEGRITY_MISMATCH")
        self.assertEqual(scanned.sha256, "0" * 64)

    def test_expired_write_cleanup_never_deletes_committed_file(self):
        transfer = reserve_write(self.dataset, 1, "abandoned")
        path = Path(self.temp.name) / (transfer.object_key + ".part")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        DatasetTransfer.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(cleanup_expired_writes(), 1)
        self.assertFalse(path.exists())
        self.assertTrue((Path(self.temp.name) / self.file.object_key).exists())
