"""Actual Dataset URL/view/serializer services under real owner authentication.

Collection creation/upload/download admission is not bypassed by this suite.
Existing records are fixtures; no fake entitlement or resource runner is used.
"""
from tempfile import TemporaryDirectory
from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.datasets.models import Dataset, DatasetFile, DatasetFileIndex, MediaAsset
from apps.tenancy.models import Tenant, Project
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


class PersonalDatasetHTTPTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="owner@example.test", password=PASSWORD)
        cls.token = Token.objects.create(user=cls.row.owner)
        cls.dataset = Dataset.objects.create(tenant=cls.row.tenant, project=cls.row.project,
            name="Owned collection", created_by=cls.row.owner)
        cls.file = DatasetFile.objects.create(tenant=cls.row.tenant, project=cls.row.project,
            dataset=cls.dataset, file_name="note.txt", object_key="fixture/note.txt",
            sha256="a" * 64, size_bytes=12, content_type="text/plain")

    def setUp(self):
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.token.key)
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        settings = override_settings(NEXUS_MEDIA_STORAGE_ROOT=self.folder.name,
                                     NEXUS_PUBLIC_BASE_URL="https://personal.example")
        settings.enable()
        self.addCleanup(settings.disable)

    def path(self, suffix=""):
        return f"/api/v1/datasets/{self.dataset.pk}/{suffix}"

    def test_capabilities_describe_only_personal_operations_and_real_import_policy(self):
        from apps.datasets.file_scanning import import_capabilities
        response = self.client.get("/api/v1/datasets/capabilities/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, {
            "can_create": True, "can_search": True, "can_pull": True,
            "file_import": import_capabilities(),
        })

    def test_capabilities_reject_anonymous_and_non_owner_credentials(self):
        self.client.credentials()
        self.assertEqual(self.client.get("/api/v1/datasets/capabilities/").status_code, 401)
        other = get_user_model().objects.create_user(username="capabilities-outsider")
        token = Token.objects.create(user=other)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token.key)
        # Personal bearer authentication rejects non-owner tokens before the view.
        self.assertEqual(self.client.get("/api/v1/datasets/capabilities/").status_code, 401)

    def test_directory_detail_rename_pull_and_delete(self):
        response = self.client.get("/api/v1/datasets/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row["id"] for row in response.data], [str(self.dataset.pk)])
        self.assertNotIn("pricing", response.data[0])
        response = self.client.get(self.path())
        self.assertEqual(response.status_code, 200, response.data)
        response = self.client.post(self.path("rename/"), {"name": "Renamed"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["name"], "Renamed")
        response = self.client.get(self.path("pull/"))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["files"][0]["id"], str(self.file.pk))
        response = self.client.delete(self.path())
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.client.get(self.path()).status_code, 404)

    def test_version_snapshot_and_manifest_routes_are_real(self):
        response = self.client.post(self.path("versions/"), {"release_notes": "Snapshot"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        version_id = response.data["id"]
        DatasetFile.objects.filter(pk=self.file.pk).update(sha256="b" * 64)
        response = self.client.get(self.path(f"versions/{version_id}/files/"))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["items"][0]["sha256"], "a" * 64)
        self.assertEqual(self.client.get(self.path("versions/")).status_code, 200)

    @override_settings(NEXUS_DATASET_SEARCH_BACKEND="database")
    def test_content_search_uses_real_index_and_authenticated_serialization(self):
        DatasetFileIndex.objects.create(tenant=self.row.tenant, project=self.row.project,
            dataset=self.dataset, file=self.file, index_status="indexed", content_text="personal search marker")
        for url in ("/api/v1/datasets/content-search/?q=marker", "/api/v1/datasets/search/?scope=content&q=marker"):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data[0]["dataset"]["id"], str(self.dataset.pk))
            self.assertEqual(response.data[0]["file"]["id"], str(self.file.pk))

    def test_deleted_response_does_not_grant_access_or_trust_stale_ownership(self):
        from types import SimpleNamespace
        from rest_framework import exceptions
        from nexus_personal.dataset_serializers import DatasetSerializer
        response = self.client.delete(self.path())
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["allowed_actions"], [])
        self.assertFalse(response.data["access"]["can_read"])
        self.dataset.refresh_from_db()
        other = get_user_model().objects.create_user(username="replacement")
        Dataset.objects.filter(pk=self.dataset.pk).update(created_by=other)
        request = SimpleNamespace(user=self.row.owner, META={}, query_params={})
        with self.assertRaises(exceptions.NotFound):
            DatasetSerializer(self.dataset, context={"request": request}).data

    def test_ownership_and_context_rejections_at_http_boundary(self):
        tenant = Tenant.objects.create(name="Foreign", slug="foreign")
        project = Project.objects.create(tenant=self.row.tenant, name="Foreign project")
        other = get_user_model().objects.create_user(username="other")
        for changes in ({"tenant": tenant}, {"project": project}, {"created_by": other}):
            values = dict(tenant=self.row.tenant, project=self.row.project,
                          created_by=self.row.owner, name="Hidden")
            values.update(changes)
            asset = Dataset.objects.create(**values)
            self.assertEqual(self.client.get(f"/api/v1/datasets/{asset.pk}/").status_code, 404)
        self.assertEqual(self.client.get(self.path(), HTTP_X_NEXUS_PROJECT=str(project.pk)).status_code, 403)
        self.assertEqual(self.client.get(self.path(), HTTP_X_NEXUS_TENANT=str(tenant.pk)).status_code, 403)
        other_token = Token.objects.create(user=other)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + other_token.key)
        self.assertIn(self.client.get(self.path()).status_code, (401, 403))

    def test_media_upload_sign_download_and_delete_through_product_views(self):
        response = self.client.post("/api/v1/media/assets/", {
            "file": SimpleUploadedFile("sample.txt", "真实文件".encode(), content_type="text/plain"),
            "purpose": MediaAsset.PURPOSE_CHOICES[0][0],
        }, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        asset_id = response.data["id"]
        response = self.client.post(f"/api/v1/media/assets/{asset_id}/signed-url/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        url = urlparse(response.data["url"])
        anonymous = APIClient()
        response = anonymous.get(url.path + "?" + url.query)
        self.assertEqual(response.status_code, 200)
        try:
            self.assertEqual(b"".join(response.streaming_content), "真实文件".encode())
        finally:
            response.close()
        self.assertEqual(self.client.delete(f"/api/v1/media/assets/{asset_id}/").status_code, 200)
        self.assertEqual(anonymous.get(url.path + "?" + url.query).status_code, 404)

    def test_private_routes_and_publication_do_not_exist(self):
        for path in ("/api/v1/marketplace/datasets/", "/api/v1/dataset-acquisitions/",
                     self.path("pricing/")):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.post(self.path("visibility/"), {"visibility": "public"},
                                         format="json").status_code, 404)

    def test_session_write_keeps_csrf_and_anonymous_cannot_list(self):
        anonymous = APIClient()
        self.assertIn(anonymous.get("/api/v1/datasets/").status_code, (401, 403))
        session = APIClient(enforce_csrf_checks=True)
        session.force_login(self.row.owner)
        self.assertEqual(session.get(self.path()).status_code, 200)
        response = session.post(self.path("rename/"), {"name": "Unsafe"}, format="json")
        self.assertEqual(response.status_code, 403)
