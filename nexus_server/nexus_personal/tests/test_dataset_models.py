"""Real core Dataset tables and local storage, without private apps or keys."""
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import FieldDoesNotExist
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase, override_settings
from rest_framework import exceptions

from apps.datasets.models import Dataset, DatasetFile, DatasetVersion, DatasetVersionEntry, MediaAsset
from apps.datasets.serializers import DatasetSerializer
from apps.datasets.services import save_uploaded_file, dataset_related_fields
from apps.datasets.storage_backends import get_dataset_storage_backend
from apps.common.resource_catalog import resource_context_payload, discoverable_resource_queryset
from apps.tenancy.models import Tenant, Project
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


class PersonalDatasetModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="owner@example.test", password=PASSWORD)
        cls.dataset = Dataset.objects.create(tenant=cls.row.tenant, project=cls.row.project,
                                            name="Personal files", created_by=cls.row.owner)
        cls.other = get_user_model().objects.create_user(username="other")

    def request(self):
        return SimpleNamespace(user=self.row.owner, META={}, query_params={},
                               tenant_id=str(self.row.tenant_id), project_id=str(self.row.project_id))

    def test_private_model_and_key_columns_are_absent_from_real_database(self):
        names = connection.introspection.table_names()
        self.assertIn("datasets_dataset", names)
        self.assertIn("datasets_mediaasset", names)
        self.assertNotIn("datasets_datasetpricing", names)
        self.assertFalse(any(name.startswith(("billing_", "iam_", "tokenbank_", "marketplace_", "api_keys_")) for name in names))
        with self.assertRaises(LookupError):
            apps.get_model("datasets", "DatasetPricing")
        with self.assertRaises(FieldDoesNotExist):
            MediaAsset._meta.get_field("api_key")
        self.assertNotIn("marketplace_logo", dict(MediaAsset.PURPOSE_CHOICES))

    def test_personal_migration_graph_has_no_private_dependency(self):
        loader = MigrationLoader(connection)
        loader.graph.validate_consistency()
        self.assertFalse(loader.detect_conflicts())
        self.assertFalse({"billing", "iam", "tokenbank", "marketplace", "api_keys"} & loader.migrated_apps)
        for model in apps.get_app_config("datasets").get_models():
            for field in model._meta.fields:
                if field.is_relation:
                    self.assertIsNotNone(field.related_model)

    def test_real_dataset_serialization_never_queries_missing_pricing(self):
        dataset = Dataset.objects.select_related(*dataset_related_fields()).get(pk=self.dataset.pk)
        payload = DatasetSerializer(dataset, context={"request": self.request()}).data
        self.assertEqual(payload["lifecycle_status"], "draft")
        self.assertEqual(payload["access"]["sources"], ["personal_owner"])
        self.assertIn("import_asset", payload["allowed_actions"])
        self.assertNotIn("pricing", payload)
        self.assertNotIn("publication_readiness", payload)

    def test_real_catalog_excludes_foreign_tenant_project_creator_and_deleted_rows(self):
        tenant = Tenant.objects.create(name="Other", slug="other")
        project = Project.objects.create(tenant=self.row.tenant, name="Other project")
        rows = [
            Dataset.objects.create(tenant=tenant, name="Foreign tenant", created_by=self.row.owner),
            Dataset.objects.create(tenant=self.row.tenant, project=project, name="Foreign project", created_by=self.row.owner),
            Dataset.objects.create(tenant=self.row.tenant, project=self.row.project, name="Foreign owner", created_by=self.other),
            Dataset.objects.create(tenant=self.row.tenant, name="Deleted", status="deleted", created_by=self.row.owner),
        ]
        queryset = discoverable_resource_queryset(Dataset.objects.all(), request=self.request(),
            tenant=self.row.tenant, resource_type="dataset")
        self.assertEqual(list(queryset.values_list("pk", flat=True)), [self.dataset.pk])
        for dataset in rows:
            with self.assertRaises(exceptions.NotFound):
                resource_context_payload(request=self.request(), resource_type="dataset", obj=dataset)

    def test_stale_object_cannot_hide_changed_creator(self):
        Dataset.objects.filter(pk=self.dataset.pk).update(created_by=self.other)
        with self.assertRaises(exceptions.NotFound):
            resource_context_payload(request=self.request(), resource_type="dataset", obj=self.dataset)

    def test_file_and_immutable_version_inherit_context_and_retain_manifest(self):
        file = DatasetFile.objects.create(dataset=self.dataset, uploaded_by=self.row.owner,
            file_name="input.txt", storage_path="fixture/input.txt", size_bytes=3, sha256="a" * 64)
        version = DatasetVersion.objects.create(dataset=self.dataset, version="v1", created_by=self.row.owner,
                                                file_count=1, size_bytes=3)
        entry = DatasetVersionEntry.objects.create(version=version, file_id=file.pk,
            created_at=file.created_at, payload={"file_id": str(file.pk), "sha256": file.sha256})
        self.assertEqual(file.project_id, self.row.project_id)
        self.assertEqual(version.tenant_id, self.row.tenant_id)
        file.sha256 = "b" * 64
        file.save()
        entry.refresh_from_db()
        self.assertEqual(entry.payload["sha256"], "a" * 64)

    def test_real_local_storage_roundtrip_and_traversal_denial(self):
        content = "Personal file content 中文".encode()
        with TemporaryDirectory(prefix="nexus-personal-dataset-") as directory, override_settings(
            NEXUS_DATASET_STORAGE_BACKEND="local", NEXUS_DATASET_STORAGE_ROOT=directory):
            stored = save_uploaded_file(dataset=self.dataset,
                uploaded_file=SimpleUploadedFile("input.txt", content, content_type="text/plain"))
            self.assertEqual(stored.sha256, hashlib.sha256(content).hexdigest())
            self.assertEqual(stored.size_bytes, len(content))
            self.assertTrue((Path(directory) / stored.object_key).is_file())
            backend = get_dataset_storage_backend()
            with backend.open(object_key=stored.object_key) as stream:
                self.assertEqual(stream.read(), content)
            with self.assertRaises(exceptions.ValidationError):
                backend.open(object_key="../outside.txt")
