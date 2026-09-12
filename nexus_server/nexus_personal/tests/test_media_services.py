"""Actual database/filesystem media lifecycle without private apps."""
import hashlib
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import exceptions

from apps.audit.models import AuditLog
from apps.datasets import media_services as media
from apps.datasets.models import MediaAsset
from apps.tenancy.models import Tenant, Project
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


class PersonalMediaServicesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="owner@example.test", password=PASSWORD)
        cls.other = get_user_model().objects.create_user(username="other")

    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        settings = override_settings(NEXUS_MEDIA_STORAGE_ROOT=folder.name,
                                     NEXUS_PUBLIC_BASE_URL="https://personal.example")
        settings.enable()
        self.addCleanup(settings.disable)

    def request(self, **kwargs):
        values = dict(user=self.row.owner, META={}, query_params={},
                      tenant_id=str(self.row.tenant_id), project_id=str(self.row.project_id))
        values.update(kwargs)
        return SimpleNamespace(**values)

    def upload(self, request=None, **kwargs):
        return media.upload_media_asset(request=request or self.request(),
            uploaded_file=SimpleUploadedFile("personal.txt", "个人文件内容".encode(),
                                            content_type="text/plain"),
            purpose=kwargs.pop("purpose", MediaAsset.PURPOSE_CHOICES[0][0]), **kwargs)

    def test_upload_list_signed_download_and_delete_use_real_storage_and_audit(self):
        asset = self.upload()
        self.assertEqual(asset.owner_id, self.row.owner_id)
        self.assertEqual(asset.project_id, self.row.project_id)
        self.assertEqual(asset.sha256, hashlib.sha256("个人文件内容".encode()).hexdigest())
        self.assertEqual(list(media.list_media_assets(request=self.request())), [asset])
        url = media.signed_media_url(request=self.request(), asset_id=str(asset.pk))
        token = parse_qs(urlparse(url).query)["token"][0]
        response = media.open_signed_media_content(asset_id=str(asset.pk), token=token)
        try:
            self.assertEqual(b"".join(response.streaming_content), "个人文件内容".encode())
        finally:
            response.close()
        media.delete_media_asset(request=self.request(), asset_id=str(asset.pk))
        asset.refresh_from_db()
        self.assertEqual(asset.status, "deleted")
        with self.assertRaises(media.MediaAssetNotFound):
            media.open_signed_media_content(asset_id=str(asset.pk), token=token)
        self.assertEqual(AuditLog.objects.filter(resource_id=str(asset.pk)).count(), 3)

    def test_other_user_and_forged_context_cannot_upload_or_read(self):
        asset = self.upload()
        for request in (
            self.request(user=self.other),
            self.request(META={"HTTP_X_NEXUS_TENANT": "foreign"}),
            self.request(META={"HTTP_X_NEXUS_PROJECT": "foreign"}),
            self.request(api_key=object()),
            self.request(service_account=object()),
        ):
            with self.subTest(request=request), self.assertRaises(exceptions.APIException):
                self.upload(request=request)
            with self.assertRaises(exceptions.APIException):
                media.get_media_asset(request=request, asset_id=str(asset.pk))
        self.assertEqual(MediaAsset.objects.count(), 1)
        self.assertEqual(len([path for path in self.root.rglob("*") if path.is_file()]), 1)

    def test_foreign_asset_rows_are_not_visible_or_mutable(self):
        owned = self.upload()
        tenant = Tenant.objects.create(name="Other", slug="other")
        project = Project.objects.create(tenant=self.row.tenant, name="Other")
        for change in ({"owner": self.other}, {"tenant": tenant},
                       {"project": project}, {"status": "deleted"}):
            fields = dict(owner=self.row.owner, tenant=self.row.tenant, project=self.row.project,
                          storage_path="unused", file_name="hidden.txt")
            fields.update(change)
            asset = MediaAsset.objects.create(**fields)
            with self.assertRaises(media.MediaAssetNotFound):
                media.get_mutable_media_asset(request=self.request(), asset_id=str(asset.pk))
        self.assertEqual(list(media.list_media_assets(request=self.request())), [owned])

    def test_cached_owner_is_revalidated_and_ownership_changes_apply_immediately(self):
        asset = self.upload()
        request = self.request()
        MediaAsset.objects.filter(pk=asset.pk).update(owner=self.other)
        with self.assertRaises(media.MediaAssetNotFound):
            media.get_mutable_media_asset(request=request, asset_id=str(asset.pk))
        get_user_model().objects.filter(pk=self.row.owner_id).update(is_active=False)
        with self.assertRaises(exceptions.APIException):
            self.upload(request=request)

    def test_private_purpose_and_invalid_upload_rejected_before_disk_write(self):
        with self.assertRaises(exceptions.ValidationError):
            self.upload(purpose="marketplace_logo")
        with override_settings(NEXUS_MEDIA_MAX_UPLOAD_BYTES=1), self.assertRaises(exceptions.ValidationError):
            self.upload()
        self.assertEqual(MediaAsset.objects.count(), 0)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_signed_urls_reject_tampering_expiry_mismatch_and_path_escape(self):
        asset = self.upload()
        token = media.sign_media_asset(asset=asset, expires_in=60)
        for asset_id, value in ((str(asset.pk), token + "x"), ("different", token),
                               (str(asset.pk), media.sign_media_asset(asset=asset, expires_in=-1))):
            with self.assertRaises(media.MediaAssetInvalid):
                media.open_signed_media_content(asset_id=asset_id, token=value)
        MediaAsset.objects.filter(pk=asset.pk).update(storage_path="../outside.txt")
        with self.assertRaises(media.MediaAssetInvalid):
            media.open_signed_media_content(asset_id=str(asset.pk), token=token)
        MediaAsset.objects.filter(pk=asset.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(media.MediaAssetNotFound):
            media.open_signed_media_content(asset_id=str(asset.pk), token=token)

    def test_missing_policy_cannot_write_files(self):
        with override_settings(NEXUS_MEDIA_POLICY_BACKEND=""), self.assertRaises(ImproperlyConfigured):
            self.upload()
        self.assertEqual(list(self.root.iterdir()), [])
