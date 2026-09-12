from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework import exceptions
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.datasets.models import Dataset, DatasetVersion
from apps.datasets.operations import retention_status, verify_object
from apps.tenancy.models import Tenant, Project
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


class PersonalDatasetRetentionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="owner@example.test", password=PASSWORD)
        cls.token = Token.objects.create(user=cls.row.owner)
        cls.dataset = Dataset.objects.create(tenant=cls.row.tenant, project=cls.row.project,
            name="Retained", size_bytes=123, created_by=cls.row.owner)

    def test_http_retention_uses_real_versions_and_no_acquisition_model(self):
        DatasetVersion.objects.create(dataset=self.dataset, tenant=self.row.tenant,
            project=self.row.project, version="v1", created_by=self.row.owner)
        self.dataset.import_jobs.create(tenant=self.row.tenant, requested_by=self.row.owner,
            kind="trace", inputs={}, request_key="pending-retention", state="queued")
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer " + self.token.key)
        response = client.get(f"/api/v1/datasets/{self.dataset.pk}/retention/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["version_count"], 1)
        self.assertEqual(response.data["active_imports"], 1)
        self.assertEqual(response.data["retained_bytes"], 123)
        self.assertFalse(response.data["has_acquisitions"])
        self.assertFalse(response.data["physical_deletion_enabled"])
        self.assertNotIn("Purchased", response.data["reason"])
        self.assertEqual(response.data["mode"], "preserve")

    def test_retention_rereads_current_row_and_rejects_foreign_context(self):
        Dataset.objects.filter(pk=self.dataset.pk).update(size_bytes=321)
        self.assertEqual(retention_status(self.dataset)["retained_bytes"], 321)
        other = get_user_model().objects.create_user(username="other")
        Dataset.objects.filter(pk=self.dataset.pk).update(created_by=other)
        with self.assertRaises(exceptions.NotFound):
            retention_status(self.dataset)
        tenant = Tenant.objects.create(name="Foreign", slug="foreign")
        project = Project.objects.create(tenant=self.row.tenant, name="Foreign")
        for changes in ({"tenant": tenant}, {"project": project}):
            values = dict(tenant=self.row.tenant, project=self.row.project, name="Foreign", created_by=self.row.owner)
            values.update(changes)
            with self.assertRaises(exceptions.APIException):
                retention_status(Dataset.objects.create(**values))

    def test_disabled_owner_and_deleted_dataset_do_not_expose_retention(self):
        Dataset.objects.filter(pk=self.dataset.pk).update(status="deleted")
        with self.assertRaises(exceptions.NotFound):
            retention_status(self.dataset)
        get_user_model().objects.filter(pk=self.row.owner_id).update(is_active=False)
        with self.assertRaises(exceptions.APIException):
            retention_status(self.dataset)

    def test_integrity_probe_reads_real_bytes_and_returns_no_path_or_content(self):
        import hashlib
        with TemporaryDirectory() as folder, override_settings(NEXUS_DATASET_STORAGE_ROOT=folder):
            content = b"retained local object"
            Path(folder, "object.txt").write_bytes(content)
            kwargs = dict(backend="local", key="object.txt", expected_size=len(content),
                          expected_hash=hashlib.sha256(content).hexdigest())
            self.assertEqual(verify_object(**kwargs), "VERIFIED")
            self.assertEqual(verify_object(**{**kwargs, "expected_size": 0}), "INTEGRITY_MISMATCH")
            self.assertEqual(verify_object(**{**kwargs, "key": "../outside.txt"}), "OBJECT_UNAVAILABLE")
            self.assertEqual(verify_object(**{**kwargs, "key": "missing.txt"}), "OBJECT_UNAVAILABLE")
