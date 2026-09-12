"""Real HTTP imports/downloads and durable finite capacity; no fake billing."""
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import uuid

from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.common.resource_limits import capability_state, enforce_capability, record_capability_usage, reserve_capability
from apps.datasets.models import Dataset, DatasetFile, DatasetTransfer
from apps.datasets.transfers import reserve_write, reserve_export, complete_export
from apps.tenancy.models import Tenant
from nexus_personal.models import PersonalDataExportUsage, PersonalInstallation
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


LIMITS = {"data.collections": 100, "data.files": 1000, "data.storage_gb": 10, "data.export_gb_per_30_days": 20}
CONTENT = "Community document 文本内容".encode()


class PersonalDataAdmissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="owner@example.test", password=PASSWORD)
        cls.token = Token.objects.create(user=cls.row.owner)

    def setUp(self):
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.token.key)
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        config = override_settings(NEXUS_DATASET_STORAGE_ROOT=folder.name)
        config.enable()
        self.addCleanup(config.disable)

    def limits(self, **values):
        return override_settings(NEXUS_PERSONAL_DATA_LIMITS={**LIMITS, **values})

    def collection(self):
        response = self.client.post("/api/v1/datasets/", {"name": "Personal files",
            "ownership": {"scope": "project", "project_id": str(self.row.project_id)}}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return Dataset.objects.get(pk=response.data["id"])

    def upload(self, dataset, content=CONTENT, key="upload-one"):
        return self.client.post(f"/api/v1/datasets/{dataset.pk}/push/",
            {"file": SimpleUploadedFile("document.txt", content, content_type="text/plain"),
             "rights_confirmed": True}, format="multipart", HTTP_IDEMPOTENCY_KEY=key)

    def download(self, dataset, file_id, **headers):
        return self.client.get(f"/api/v1/datasets/{dataset.pk}/files/{file_id}/download/", **headers)

    def consume(self, response):
        self.assertIn(response.status_code, (200, 206), getattr(response, "data", None))
        try:
            return b"".join(response.streaming_content)
        finally:
            response.close()

    def test_real_create_scan_upload_index_version_and_download(self):
        dataset = self.collection()
        response = self.upload(dataset)
        self.assertEqual(response.status_code, 201, response.data)
        file_id = response.data["id"]
        file = DatasetFile.objects.get(pk=file_id)
        self.assertEqual(file.metadata_json["scan_status"], "passed")
        self.assertEqual(file.index.index_status, "indexed")
        self.assertEqual((self.root / file.object_key).read_bytes(), CONTENT)
        version = self.client.post(f"/api/v1/datasets/{dataset.pk}/versions/", {}, format="json")
        self.assertEqual(version.status_code, 201, version.data)
        self.assertEqual(self.consume(self.download(dataset, file_id)), CONTENT)
        usage = PersonalDataExportUsage.objects.get()
        self.assertEqual(usage.size_bytes, len(CONTENT))
        self.assertEqual(usage.transfer.state, "completed")
        self.assertEqual(capability_state(tenant=self.row.tenant, code="data.export_gb_per_30_days")["used"],
                         Decimal(len(CONTENT)) / Decimal(1024 ** 3))

    def test_collection_limit_is_enforced_and_delete_frees_only_collection_capacity(self):
        with self.limits(**{"data.collections": 1}):
            dataset = self.collection()
            response = self.client.post("/api/v1/datasets/", {"name": "Excess"}, format="json")
            self.assertEqual(response.status_code, 409, response.data)
            self.assertEqual(Dataset.objects.count(), 1)
            uploaded = self.upload(dataset)
            self.assertEqual(uploaded.status_code, 201, uploaded.data)
            self.assertEqual(self.client.delete(f"/api/v1/datasets/{dataset.pk}/").status_code, 200)
            self.collection()
            self.assertEqual(capability_state(tenant=self.row.tenant, code="data.files")["used"], 1)

    def test_upload_idempotency_and_file_limit_do_not_duplicate_rows(self):
        dataset = self.collection()
        with self.limits(**{"data.files": 1}):
            one, replay = self.upload(dataset), self.upload(dataset)
            self.assertEqual(one.status_code, 201, one.data)
            self.assertEqual(replay.data["id"], one.data["id"])
            exceeded = self.upload(dataset, content=b"second document", key="other")
            self.assertEqual(exceeded.status_code, 409, exceeded.data)
            self.assertEqual(DatasetFile.objects.count(), 1)
            self.assertEqual(len([p for p in self.root.rglob("*") if p.is_file()]), 1)

    def test_storage_limit_and_secret_scan_reject_without_writing(self):
        dataset = self.collection()
        with self.limits(**{"data.storage_gb": 0}):
            self.assertEqual(self.upload(dataset).status_code, 409)
        secret = self.upload(dataset, content=b"Contact test-user@example.invalid", key="secret")
        self.assertEqual(secret.status_code, 400, secret.data)
        self.assertIn("FILE_SENSITIVE_CONTENT", str(secret.data))
        self.assertFalse(DatasetFile.objects.exists())
        self.assertFalse(any(p.is_file() for p in self.root.rglob("*")))

    def test_real_image_scan_and_download_preserve_original_bytes(self):
        from io import BytesIO
        from PIL import Image
        stream = BytesIO()
        Image.new("RGB", (8, 8), color="blue").save(stream, format="PNG")
        content = stream.getvalue()
        dataset = self.collection()
        response = self.client.post(f"/api/v1/datasets/{dataset.pk}/push/",
            {"file": SimpleUploadedFile("picture.png", content, content_type="image/png"),
             "rights_confirmed": True}, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["content_type"], "image/png")
        self.assertEqual(response.data["metadata_json"]["scan_metadata"]["scope"], "raster_integrity_and_metadata")
        self.assertEqual(self.consume(self.download(dataset, response.data["id"])), content)

    def test_pending_write_reservations_count_and_expire(self):
        dataset = self.collection()
        with self.limits(**{"data.files": 1}):
            first = reserve_write(dataset, 10, "first.txt")
            with self.assertRaises(exceptions.APIException):
                reserve_write(dataset, 10, "second.txt")
            DatasetTransfer.objects.filter(pk=first.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
            second = reserve_write(dataset, 10, "second.txt")
            self.assertNotEqual(first.pk, second.pk)

    def test_download_retries_ranges_and_restart_style_reads_are_idempotent(self):
        dataset = self.collection()
        file_id = self.upload(dataset).data["id"]
        identity = str(uuid.uuid4())
        self.assertEqual(self.consume(self.download(dataset, file_id, HTTP_X_NEXUS_DOWNLOAD_ID=identity)), CONTENT)
        self.assertEqual(self.consume(self.download(dataset, file_id, HTTP_X_NEXUS_DOWNLOAD_ID=identity,
                                                    HTTP_RANGE="bytes=0-3")), CONTENT[:4])
        self.assertEqual(PersonalDataExportUsage.objects.count(), 1)
        # Recreating the backend reads the database ledger, not an in-memory counter.
        from nexus_personal.resource_limits import PersonalResourceAdmission
        self.assertGreater(PersonalResourceAdmission().capability_state(
            tenant=self.row.tenant, code="data.export_gb_per_30_days")["used"], 0)

    def test_download_limit_and_interruption_keep_reservation_not_false_usage(self):
        dataset = self.collection()
        file_id = self.upload(dataset).data["id"]
        with self.limits(**{"data.export_gb_per_30_days": 0}):
            self.assertEqual(self.download(dataset, file_id).status_code, 409)
        response = self.download(dataset, file_id)
        self.assertEqual(response.status_code, 200)
        response.close()
        self.assertFalse(PersonalDataExportUsage.objects.exists())
        transfer = DatasetTransfer.objects.get(kind="export")
        DatasetTransfer.objects.filter(pk=transfer.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(exceptions.ValidationError):
            complete_export(transfer)
        self.assertEqual(self.consume(self.download(dataset, file_id)), CONTENT)

    def test_usage_report_must_match_real_transfer_and_rolls_back(self):
        dataset = self.collection()
        transfer = reserve_export(dataset, 100, uuid.uuid4().hex)
        kwargs = dict(tenant=self.row.tenant, code="data.export_gb_per_30_days",
                      amount=Decimal(100) / Decimal(1024 ** 3),
                      idempotency_key="dataset-export:" + transfer.identity,
                      metadata={"dataset_id": str(dataset.pk)})
        for changes in ({"amount": "1"}, {"metadata": {"dataset_id": "foreign"}},
                        {"metadata": ["invalid"]},
                        {"idempotency_key": "dataset-export:unknown"}):
            with self.assertRaises(exceptions.ValidationError):
                record_capability_usage(**{**kwargs, **changes})
        self.assertFalse(PersonalDataExportUsage.objects.exists())
        from django.db import transaction
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                complete_export(transfer)
                raise RuntimeError("Abort transaction")
        self.assertFalse(PersonalDataExportUsage.objects.exists())
        transfer.refresh_from_db()
        self.assertEqual(transfer.state, "active")
        complete_export(transfer)
        complete_export(transfer)
        self.assertEqual(PersonalDataExportUsage.objects.count(), 1)

    def test_period_rollover_uses_installation_anchor_and_downgrade_retains_files(self):
        dataset = self.collection()
        file_id = self.upload(dataset).data["id"]
        self.consume(self.download(dataset, file_id))
        now = timezone.now()
        PersonalInstallation.objects.filter(pk=1).update(created_at=now - timedelta(days=35))
        PersonalDataExportUsage.objects.update(recorded_at=now - timedelta(days=10))
        state = capability_state(tenant=self.row.tenant, code="data.export_gb_per_30_days")
        self.assertEqual(state["used"], 0)
        self.assertGreater(state["reset_at"], now)
        with self.limits(**{"data.files": 0}):
            self.assertEqual(capability_state(tenant=self.row.tenant, code="data.files")["state"], "over_limit")
            self.assertTrue(DatasetFile.objects.filter(pk=file_id).exists())

    def test_invalid_configuration_context_and_unimplemented_operations_fail_closed(self):
        for value in (None, -1, "NaN", "Infinity", True, 1.5, "1.2"):
            with self.limits(**{"data.files": value}), self.assertRaises(ImproperlyConfigured):
                capability_state(tenant=self.row.tenant, code="data.files")
        tenant = Tenant.objects.create(name="Foreign", slug="foreign")
        with self.assertRaises(exceptions.NotFound):
            enforce_capability(tenant=tenant, code="data.collections")
        with self.assertRaises(ImproperlyConfigured):
            enforce_capability(tenant=self.row.tenant, code="agents.unknown")
        with self.assertRaises(ImproperlyConfigured):
            reserve_capability(tenant=self.row.tenant, code="data.files", idempotency_key="not-the-transfer-protocol")
