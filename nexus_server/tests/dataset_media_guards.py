"""Shared raster, file import and protected output assertions; no commercial host."""
import base64
import hashlib
import io
from unittest import mock

from PIL import Image, PngImagePlugin
from django.test import override_settings
from rest_framework.test import APIClient

from apps.agents.models import Agent, AgentDisplayRun, AgentOutputArtifact
from apps.agents.runtime_services import create_run_display_asset
from apps.agents.services import append_display_event, sync_output_artifacts_from_run
from apps.datasets.file_scanning import scan_asset_stream
from apps.datasets.models import DatasetFile, DatasetQuota


def raster(format="PNG", metadata=None):
    stream = io.BytesIO()
    Image.new("RGB", (8, 6), "green").save(stream, format=format, **({"pnginfo": metadata} if metadata else {}))
    return stream.getvalue()


class AssetScanGuards:
    def test_supported_rasters_and_text(self):
        for suffix, mime, data in [("png", "image/png", raster()), ("jpg", "image/jpeg", raster("JPEG")),
                ("webp", "image/webp", raster("WEBP")), ("txt", "text/plain", b"safe text"),
                ("json", "application/json", b'{"ok": true}'), ("csv", "text/csv", b"name,value\nsample,2")]:
            with self.subTest(suffix=suffix):
                result = scan_asset_stream(io.BytesIO(data), file_name=f"output.{suffix}", content_type=mime)
                self.assertEqual(result["error_code"], "")
                self.assertEqual(result["sha256"], hashlib.sha256(data).hexdigest())

    def test_rejects_forgery_truncation_active_files_and_unknown_binary(self):
        cases = [("bad.png", "image/png", b"<svg><script>alert(1)</script></svg>"),
                 ("bad.jpg", "image/jpeg", raster()), ("bad.png", "text/plain", raster()),
                 ("bad.png", "image/png", raster()[:25]), ("bad.svg", "image/svg+xml", b"<svg/>"),
                 ("bad.html", "text/html", b"<script>alert(1)</script>"),
                 ("bad.exe", "application/octet-stream", b"MZ executable"),
                 ("bad.pdf", "application/pdf", b"%PDF-1.7"), ("bad.txt", "text/plain", b"\x00binary")]
        for name, mime, data in cases:
            with self.subTest(name=name, mime=mime):
                self.assertTrue(scan_asset_stream(io.BytesIO(data), file_name=name, content_type=mime)["error_code"])

    @override_settings(NEXUS_IMAGE_MAX_PIXELS=8)
    def test_rejects_pixel_bomb(self):
        self.assertEqual(scan_asset_stream(io.BytesIO(raster()), file_name="large.png")["error_code"], "IMAGE_INVALID")

    def test_detects_secrets_in_text_and_image_metadata_without_echo(self):
        # Use a pattern the current shared redaction rules explicitly recognize.
        text = b"sk-" + b"a" * 48
        result = scan_asset_stream(io.BytesIO(text), file_name="secret.txt")
        self.assertTrue(result["error_code"])
        info = PngImagePlugin.PngInfo()
        info.add_text("Comment", text.decode())
        result = scan_asset_stream(io.BytesIO(raster(metadata=info)), file_name="secret.png")
        self.assertEqual(result["error_code"], "IMAGE_METADATA_SENSITIVE")
        self.assertNotIn(text.decode(), str(result))

    def test_rejects_animated_images(self):
        picture = Image.new("RGB", (8, 6), "red")
        data = io.BytesIO()
        picture.save(data, format="PNG", save_all=True, append_images=[Image.new("RGB", (8, 6), "blue")], duration=100)
        self.assertEqual(scan_asset_stream(io.BytesIO(data.getvalue()), file_name="animated.png")["error_code"], "IMAGE_INVALID")

    def test_rejects_gps_metadata(self):
        picture = Image.new("RGB", (8, 6), "red")
        exif = Image.Exif()
        exif[34853] = {1: "N", 2: (1.0, 2.0, 3.0)}
        data = io.BytesIO()
        picture.save(data, format="JPEG", exif=exif)
        self.assertEqual(scan_asset_stream(io.BytesIO(data.getvalue()), file_name="gps.jpg")["error_code"], "IMAGE_METADATA_SENSITIVE")


class DatasetMediaImportGuards:
    def assert_generated_image_scan_capture_and_preview(self):
        agent, run = self.image_run_fixture()
        path = f"/api/v1/internal/agent-runs/{run.id}/display-assets/"
        internal = APIClient()
        body = {"content_type": "image/png", "content_base64": base64.b64encode(raster()).decode()}
        self.assertEqual(internal.post(path, body, format="json", HTTP_X_NEXUS_INTERACTION_TOKEN="incorrect").status_code, 404)
        uploaded = internal.post(path, body, format="json", HTTP_X_NEXUS_INTERACTION_TOKEN="test-only")
        self.assertEqual(uploaded.status_code, 201, uploaded.content)
        result = self.media_payload(uploaded)
        self.assertFalse(run.output_artifacts.exists())
        # The real SDK first uploads bytes, then reports an explicit output event.
        event_body = {"type": "CUSTOM", "name": "nexus.image.created", "value": {"type": "image", "asset_id": result["id"]}}
        emitted = internal.post(f"/api/v1/internal/ag-ui/runs/{run.id}/events/", event_body,
            format="json", HTTP_X_NEXUS_AGUI_TOKEN="write-test-only", HTTP_X_NEXUS_AGUI_EVENT_ID="image-result")
        self.assertEqual(emitted.status_code, 201, emitted.content)
        artifact = run.output_artifacts.get()
        sync_output_artifacts_from_run(run=run)
        self.assertEqual(run.output_artifacts.count(), 1)
        # Generic file events cannot downgrade caller privacy or provenance.
        append_display_event(run=run, event_type="CUSTOM", payload={"name": "nexus.file.created",
            "value": {"path": artifact.workspace_path, "producer_step": "other"}})
        artifact.refresh_from_db()
        self.assertEqual(artifact.producer_step, f"display_image:{result['id']}")
        self.assertEqual(artifact.snapshot_status, "ready")
        self.assertEqual(artifact.sha256, hashlib.sha256(raster()).hexdigest())
        self.assertEqual(artifact.scan_status, "pending")
        run.status = "completed"
        run.save()
        scanned_response = self.client.post(f"/api/v1/agents/{agent.id}/display-runs/{run.id}/outputs/{artifact.id}/scan/", {}, format="json", **self.headers)
        self.assertEqual(scanned_response.status_code, 200, scanned_response.content)
        scanned = AgentOutputArtifact.objects.get(pk=artifact.pk)
        self.assertEqual(scanned.scan_status, "passed", scanned.scan_metadata)
        response = self.client.post(f"/api/v1/datasets/{self.dataset.id}/agent-assets/artifact/",
            {"agent_id": str(agent.id), "artifact_id": str(artifact.id)}, format="json", **self.headers)
        self.assertEqual(response.status_code, 201, response.content)
        item = self.media_payload(response)
        self.assertEqual(item["sha256"], artifact.sha256)
        self.assertEqual(item["metadata_json"]["source_type"], "agent_artifact")
        self.assertEqual(self.client.get(item["download_url"] + "?preview=1", **self.headers).status_code, 200)
        return agent, artifact

    def test_upload_roundtrip_provenance_and_readiness(self):
        for name, data, mime in [("sample.png", raster(), "image/png"), ("document.txt", b"plain text", "text/plain")]:
            response = self.upload(name, data, mime)
            self.assertEqual(response.status_code, 201, response.content)
            item = self.media_payload(response)
            file = DatasetFile.objects.get(pk=item["id"])
            self.assertEqual(file.metadata_json["source_type"], "user_upload")
            self.assertEqual(file.metadata_json["scan_status"], "passed")
            self.assert_file_publication(file)
            downloaded = self.client.get(item["download_url"], **self.headers)
            self.assertEqual(b"".join(downloaded.streaming_content), data)
        self.dataset.refresh_from_db()
        self.assertEqual(self.dataset.file_count, 2)

    def test_upload_requires_consent_and_cannot_forge_scan(self):
        response = self.upload(rights_confirmed="false", scan_status="passed")
        self.assertEqual(response.status_code, 400)
        response = self.upload(data=b"not a PNG", scan_status="passed", policy_status="approved")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.dataset.files.exists())

    def test_empty_oversize_and_quota_rejections_leave_no_asset(self):
        self.assertEqual(self.upload(data=b"").status_code, 400)
        with override_settings(NEXUS_DATASET_IMPORT_MAX_BYTES=4):
            self.assertEqual(self.upload().status_code, 400)
        DatasetQuota.objects.create(dataset=self.dataset, max_size_bytes=1, raw_value="1B")
        self.assertEqual(self.upload().status_code, 400)
        self.assertFalse(self.dataset.files.exists())

    def test_retries_are_idempotent_and_conflicting_bytes_rejected(self):
        self.headers["HTTP_IDEMPOTENCY_KEY"] = "same-upload"
        first, second = self.upload(), self.upload()
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(self.media_payload(first)["id"], self.media_payload(second)["id"])
        self.assertEqual(self.upload(name="different.png").status_code, 400)
        self.assertEqual(self.dataset.files.count(), 1)

    def test_preview_strips_metadata_and_does_not_change_download(self):
        info = PngImagePlugin.PngInfo()
        info.add_text("Comment", "original author metadata")
        source = raster(metadata=info)
        item = self.media_payload(self.upload(data=source))
        response = self.client.get(item["download_url"] + "?preview=1", **self.headers)
        self.assertEqual(response.status_code, 200, response.content[:200])
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        with Image.open(io.BytesIO(response.content)) as image:
            self.assertNotIn("Comment", image.info)
        self.assertEqual(b"".join(self.client.get(item["download_url"], **self.headers).streaming_content), source)

    def test_text_and_corrupted_image_cannot_be_previewed(self):
        item = self.media_payload(self.upload("file.txt", b"text", "text/plain"))
        self.assertEqual(self.client.get(item["download_url"] + "?preview=1", **self.headers).status_code, 404)
        item = self.media_payload(self.upload())
        DatasetFile.objects.filter(pk=item["id"]).update(sha256="0" * 64)
        self.assertEqual(self.client.get(item["download_url"] + "?preview=1", **self.headers).status_code, 400)

    def test_release_preview_remains_immutable(self):
        item = self.media_payload(self.upload())
        version = self.client.post(f"/api/v1/datasets/{self.dataset.id}/versions/", {}, format="json", **self.headers)
        self.assertEqual(version.status_code, 201, version.content)
        version_id = self.media_payload(version)["id"]
        DatasetFile.objects.filter(pk=item["id"]).update(content_type="text/plain", sha256="0" * 64)
        response = self.client.get(f"/api/v1/datasets/{self.dataset.id}/versions/{version_id}/files/{item['id']}/download/?preview=1", **self.headers)
        self.assertEqual(response.status_code, 200, response.content[:200])

    def test_browser_frames_and_foreign_assets_are_not_output_images(self):
        agent = Agent.objects.create(tenant=self.tenant, name="Browser", created_by=self.owner)
        run = AgentDisplayRun.objects.create(tenant=self.tenant, agent=agent, run_kind="invocation", status="running")
        with mock.patch("apps.agents.runtime_services.get_internal_interaction_run", return_value=run):
            result = create_run_display_asset(run_id=str(run.id), token="test-only", data={
                "content_type": "image/png", "content_base64": base64.b64encode(raster()).decode()})
        append_display_event(run=run, event_type="CUSTOM", payload={"name": "nexus.computer.frame",
            "value": {"asset_id": result["id"], "screenshot_url": result["url"]}})
        sync_output_artifacts_from_run(run=run)
        self.assertFalse(run.output_artifacts.exists())
        foreign_run = AgentDisplayRun.objects.create(tenant=self.tenant, agent=agent, run_kind="invocation", status="running")
        for asset_id in (result["id"], "invalid"):
            append_display_event(run=foreign_run, event_type="CUSTOM", payload={"name": "nexus.image.created",
                "value": {"asset_id": asset_id}})
        self.assertFalse(foreign_run.output_artifacts.exists())
