"""Actual owner Images HTTP calls, persisted artifacts and no private imports."""
import base64
import hashlib
from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from unittest.mock import patch
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient
from apps.datasets.models import MediaAsset
from apps.gateway.models import GatewayImageOperation, GatewayRequestLog
from apps.common.gateway_lifecycle import expire_image_operations
from nexus_personal.models import PersonalGatewayRequest
from .provider_http_fixture import ProviderHTTPFixture
from . import test_router_runtime as runtime_tests
from tests.image_media_guards import GatewayImageHTTPGuards


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1")
class PersonalImageExecutionTests(GatewayImageHTTPGuards, ProviderHTTPFixture, TestCase):
    source = runtime_tests.PersonalRouterRuntimeTests.source
    post = runtime_tests.PersonalRouterRuntimeTests.post
    runtime = runtime_tests.PersonalRouterRuntimeTests.runtime

    @property
    def image_requests(self):
        return self.calls

    def setUp(self):
        runtime_tests.PersonalRouterRuntimeTests.setUp(self)
        output = BytesIO()
        Image.new("RGB", (4, 4), "red").save(output, "PNG")
        type(self).generated_image = output.getvalue()
        model = self.source_row.canonical_model
        model.operations = ["images.generate", "images.edit", "images.variation"]
        model.input_modalities, model.output_modalities = ["text", "image"], ["image"]
        model.save()
        offer = self.source_row.runtime_model_offer
        offer.metadata = {"model_contract": {"operations": model.operations,
            "input_modalities": model.input_modalities, "output_modalities": model.output_modalities}}
        offer.save()
        folder = TemporaryDirectory(prefix="personal-image-output-")
        self.addCleanup(folder.cleanup)
        config = override_settings(NEXUS_MEDIA_STORAGE_ROOT=folder.name)
        config.enable()
        self.addCleanup(config.disable)

    def call(self, key="image-1", endpoint="generations", **changes):
        return self.client.post(f"/api/v1/openai/v1/images/{endpoint}", {"model": self.model,
            "router_id": str(self.router.pk), "prompt": "private-image-prompt", **changes},
            format="json", HTTP_IDEMPOTENCY_KEY=key, **self.headers)

    def test_real_generation_artifacts_usage_and_replay_without_regeneration(self):
        response = self.call(response_format="b64_json", n=2)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(base64.b64decode(response.data["data"][0]["b64_json"]), self.generated_image)
        self.assertEqual(response.data["usage"], {"images": 1})
        operation = GatewayImageOperation.objects.get()
        self.assertEqual(operation.state, "succeeded")
        self.assertEqual(operation.gateway_log.operation, "images.generate")
        self.assertEqual(operation.gateway_log.image_count, 1)
        self.assertEqual(PersonalGatewayRequest.objects.get().state, "completed")
        self.assertEqual(self.call(response_format="b64_json", n=2).status_code, 200)
        self.assertEqual(self.call(response_format="b64_json", n=1).status_code, 409)
        self.assertEqual(len(self.calls), 1)
        persisted = str(list(GatewayImageOperation.objects.values())) + str(list(PersonalGatewayRequest.objects.values()))
        self.assertNotIn("private-image-prompt", persisted)
        self.assertNotIn("pricing_snapshot", persisted)
        self.assertEqual(MediaAsset.objects.get().sha256, hashlib.sha256(self.generated_image).hexdigest())

    def test_image_catalog_contract_tracks_available_source_and_recovers(self):
        def catalog(**params):
            response = self.client.get("/api/v1/openai/v1/models", params)
            self.assertEqual(response.status_code, 200, response.data)
            return response.data["data"]

        expected = {"operations": ["images.edit", "images.generate", "images.variation"],
                    "input_modalities": ["image", "text"], "output_modalities": ["image"]}
        for params, model in (({}, self.group.name), ({"router_id": str(self.router.pk)}, self.model)):
            rows = catalog(**params)
            self.assertEqual([row["id"] for row in rows], [model])
            self.assertEqual(rows[0]["model_contract"], expected)
            self.assertNotIn("local-provider-test-key", str(rows))
            self.assertNotIn("endpoint", str(rows))
        self.source_row.health_status = "unhealthy"
        self.source_row.save(update_fields=["health_status"])
        self.assertEqual(catalog(), [])
        self.assertEqual(catalog(router_id=str(self.router.pk)), [])
        self.assertEqual(self.calls, [])
        self.source_row.health_status = "healthy"
        self.source_row.save(update_fields=["health_status"])
        self.assertEqual(catalog(router_id=str(self.router.pk))[0]["model_contract"], expected)
        self.assertEqual(self.call("recovered-image").status_code, 200)
        self.assertEqual(len(self.calls), 1)

    def test_private_provider_egress_requires_explicit_permission_without_replay(self):
        with override_settings(NEXUS_PROVIDER_ALLOW_HTTP=False, NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS=""):
            response = self.call("egress-denied")
            self.assertEqual(response.status_code, 502, response.data)
            self.assertEqual(self.calls, [])
            self.assertFalse(GatewayRequestLog.objects.filter(status="success").exists())
            self.assertFalse(MediaAsset.objects.filter(status="active").exists())
            self.assertNotIn("local-provider-test-key", str(response.data))
        # Restoring the test-only permission cannot replay an uncertain POST.
        self.assertEqual(self.call("egress-denied").status_code, 409)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.call("egress-restored").status_code, 200)
        self.assertEqual(len(self.calls), 1)

    def test_signed_download_and_owned_asset_reuse(self):
        response = self.call()
        self.assertEqual(response.status_code, 200, response.data)
        parsed = urlsplit(response.data["data"][0]["url"])
        downloaded = self.client.get(parsed.path + "?" + parsed.query)
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(b"".join(downloaded.streaming_content), self.generated_image)
        asset = MediaAsset.objects.get()
        response = self.call("edit", "edits", image="nexus-media://" + str(asset.pk))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.image_upload_hashes[-1], ("image", hashlib.sha256(self.generated_image).hexdigest()))
        MediaAsset.objects.filter(pk=asset.pk).update(owner=self.other)
        self.assertEqual(self.call("foreign-edit", "edits", image="nexus-media://" + str(asset.pk)).status_code, 404)
        self.assertEqual(len(self.calls), 2)

    def test_multipart_edit_and_variation_validate_real_uploaded_bytes(self):
        for endpoint, operation in (("edits", "images.edit"), ("variations", "images.variation")):
            payload = {"model": self.model, "router_id": str(self.router.pk), "image": SimpleUploadedFile("input.png", self.generated_image, content_type="image/png")}
            if endpoint == "edits":
                payload["prompt"] = "edit input"
            response = self.client.post(f"/api/v1/openai/v1/images/{endpoint}", payload, format="multipart",
                HTTP_IDEMPOTENCY_KEY=endpoint, **self.headers)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertTrue(GatewayRequestLog.objects.filter(operation=operation, status="success").exists())
            payload["image"] = SimpleUploadedFile("renamed.png", self.generated_image, content_type="image/png")
            replay = self.client.post(f"/api/v1/openai/v1/images/{endpoint}", payload, format="multipart",
                HTTP_IDEMPOTENCY_KEY=endpoint, **self.headers)
            self.assertEqual(replay.status_code, 200, replay.data)
            changed = BytesIO()
            Image.new("RGB", (4, 4), "blue").save(changed, "PNG")
            payload["image"] = SimpleUploadedFile("input.png", changed.getvalue(), content_type="image/png")
            conflict = self.client.post(f"/api/v1/openai/v1/images/{endpoint}", payload, format="multipart",
                HTTP_IDEMPOTENCY_KEY=endpoint, **self.headers)
            self.assertEqual(conflict.status_code, 409, conflict.data)
        self.assertEqual(len(self.image_upload_hashes), 2)

    def test_invalid_outputs_and_private_url_never_leave_success_or_retry(self):
        for mode in ("empty", "malformed", "private_url"):
            type(self).image_mode = mode
            response = self.call(mode)
            self.assertEqual(response.status_code, 502, response.data)
            self.assertNotIn("127.0.0.1", str(response.data))
            before = len(self.calls)
            self.assertEqual(self.call(mode).status_code, 409)
            self.assertEqual(len(self.calls), before)
        self.assertFalse(GatewayImageOperation.objects.filter(state="succeeded").exists())
        self.assertFalse(MediaAsset.objects.filter(status="active").exists())
        self.assertEqual(PersonalGatewayRequest.objects.filter(state="interrupted").count(), 3)

    def test_input_validation_and_scoped_router_credential_cannot_read_owner_media(self):
        self.assertEqual(self.call("").status_code, 400)
        self.assertEqual(self.call(stream=True).status_code, 400)
        self.assertEqual(self.call("bad-ref", "edits", image="https://127.0.0.1/private").status_code, 400)
        token = self.post(f"/api/v1/routers/{self.router.pk}/export-credentials/", {}).data["gateway_api_key"]
        limited = APIClient()
        limited.credentials(HTTP_AUTHORIZATION="Bearer " + token)
        self.assertEqual(limited.post("/api/v1/openai/v1/images/generations", {"model": self.model, "prompt": "x"},
            format="json", HTTP_IDEMPOTENCY_KEY="limited").status_code, 403)
        self.assertEqual(self.calls, [])

    def test_failure_after_upload_removes_exact_outputs_and_preserves_replay_fence(self):
        with patch("nexus_personal.gateway_lifecycle.finalize", side_effect=RuntimeError("write failed")):
            self.assertEqual(self.call("write-failure").status_code, 502)
        self.assertFalse(MediaAsset.objects.filter(status="active").exists())
        from apps.datasets.media_services import media_storage_root
        self.assertEqual([p for p in media_storage_root().rglob("*") if p.is_file()], [])
        self.assertEqual(self.call("write-failure").status_code, 409)
        self.assertEqual(len(self.calls), 1)

    def test_expiry_marks_pending_operation_uncertain_without_invoking(self):
        operation = GatewayImageOperation.objects.create(tenant=self.installation.tenant,
            principal=f"user:{self.installation.owner.pk}", key_digest="a" * 64, request_digest="b" * 64, operation="images.generate")
        GatewayImageOperation.objects.filter(pk=operation.pk).update(created_at=timezone.now() - timedelta(minutes=20))
        self.assertEqual(expire_image_operations(), {"expired": 1})
        operation.refresh_from_db()
        self.assertEqual(operation.state, "failed")
        self.assertEqual(operation.error_code, "IMAGE_RESULT_UNCERTAIN")
        self.assertEqual(expire_image_operations(), {"expired": 0})
        self.assertEqual(self.calls, [])

    def test_locked_failed_output_cleanup_is_recorded_and_can_be_retried(self):
        with patch("nexus_personal.gateway_lifecycle.finalize", side_effect=RuntimeError("write failed")), \
             patch("pathlib.Path.unlink", side_effect=PermissionError("do not echo local paths")):
            response = self.call("locked-output")
        self.assertEqual(response.status_code, 502, response.data)
        self.assertNotIn("local paths", str(response.data))
        self.assertEqual(GatewayImageOperation.objects.get().error_code, "IMAGE_CLEANUP_PENDING")
        self.assertFalse(MediaAsset.objects.filter(status="active").exists())
        self.assertEqual(expire_image_operations(), {"expired": 0})
        from apps.datasets.media_services import media_storage_root
        self.assertEqual([p for p in media_storage_root().rglob("*") if p.is_file()], [])
        self.assertEqual(self.call("locked-output").status_code, 409)
        self.assertEqual(len(self.calls), 1)

    def test_interrupted_upload_expiry_cleans_outputs_without_touching_other_images(self):
        self.assertEqual(self.call("keep").status_code, 200)
        kept = MediaAsset.objects.get()
        # An interruption outside Exception handlers leaves an admitted pending
        # image with files, rather than a normal handled failure.
        with patch("nexus_personal.gateway_lifecycle.finalize", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.call("interrupted")
        pending = GatewayImageOperation.objects.get(state="pending")
        GatewayImageOperation.objects.filter(pk=pending.pk).update(created_at=timezone.now() - timedelta(minutes=20))
        lease = PersonalGatewayRequest.objects.get(request_id=str(pending.pk))
        PersonalGatewayRequest.objects.filter(pk=lease.pk).update(state="dispatched", expires_at=timezone.now() + timedelta(minutes=1))
        self.assertEqual(expire_image_operations(), {"expired": 0})
        self.assertEqual(MediaAsset.objects.filter(status="active").count(), 2)
        PersonalGatewayRequest.objects.filter(pk=lease.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(expire_image_operations(), {"expired": 1})
        self.assertEqual(list(MediaAsset.objects.filter(status="active").values_list("pk", flat=True)), [kept.pk])
        from apps.datasets.media_services import media_storage_root
        self.assertEqual([p for p in media_storage_root().rglob("*") if p.is_file()], [media_storage_root() / kept.storage_path])
        self.assertEqual(self.call("interrupted").status_code, 409)
        self.assertEqual(len(self.calls), 2)
