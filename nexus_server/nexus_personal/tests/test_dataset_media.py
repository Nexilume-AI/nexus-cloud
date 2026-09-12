"""Real owner-scoped uploads and protected previews with shared safety guards."""
import base64
from tempfile import TemporaryDirectory
from datetime import timedelta
from types import SimpleNamespace

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import NotFound
from rest_framework.test import APIClient

from apps.datasets.models import Dataset
from apps.datasets.services import validate_dataset_file_publication
from apps.agents.models import Agent, AgentDisplayRun
from apps.common.subjects import request_subject, hash_token
from nexus_personal.services import provision_owner
from tests.dataset_media_guards import AssetScanGuards, DatasetMediaImportGuards, raster
from .test_installation import PASSWORD


class PersonalAssetScanTests(AssetScanGuards, SimpleTestCase):
    pass


class PersonalDatasetMediaTests(DatasetMediaImportGuards, TestCase):
    def test_agent_generated_image_scan_capture_and_preview(self):
        self.assert_generated_image_scan_capture_and_preview()

    def test_image_capture_requires_current_complete_caller_identity(self):
        agent, artifact = self.assert_generated_image_scan_capture_and_preview()
        run = artifact.run
        self.assertEqual(self.dataset.files.count(), 1)
        for field in ("caller_principal_type", "caller_principal_id", "caller_subject_hash"):
            with self.subTest(field=field):
                previous = getattr(run, field)
                self.assertTrue(previous)
                setattr(run, field, "")
                run.save(update_fields=[field])
                response = self.client.post(f"/api/v1/datasets/{self.dataset.pk}/agent-assets/artifact/",
                    {"agent_id": str(agent.pk), "artifact_id": str(artifact.pk)},
                    format="json", **self.headers)
                self.assertEqual(response.status_code, 404, response.content)
                self.assertEqual(self.dataset.files.count(), 1)
                setattr(run, field, previous)
                run.save(update_fields=[field])

    def assert_file_publication(self, dataset_file):
        with self.assertRaises(NotFound):
            validate_dataset_file_publication(dataset_file=dataset_file)

    def test_valid_invocation_token_cannot_upload_demo_image(self):
        _, run = self.image_run_fixture()
        run.run_kind = "demo"
        run.save(update_fields=["run_kind"])
        internal = APIClient()
        response = internal.post(f"/api/v1/internal/agent-runs/{run.id}/display-assets/",
            {"content_type": "image/png", "content_base64": base64.b64encode(raster()).decode()},
            format="json", HTTP_X_NEXUS_INTERACTION_TOKEN="test-only")
        self.assertEqual(response.status_code, 404, response.content)
        self.assertFalse(run.display_assets.exists())
        self.assertFalse(run.output_artifacts.exists())

    def image_run_fixture(self):
        subject = request_subject(SimpleNamespace(user=self.owner,
            tenant_id=str(self.tenant.pk), project_id=str(self.dataset.project_id), headers={}))
        agent = Agent.objects.create(tenant=self.tenant, project=self.dataset.project,
            name="Image Agent", created_by=self.owner)
        run = AgentDisplayRun.objects.create(tenant=self.tenant, consumer_tenant=self.tenant,
            consumer_project=self.dataset.project, agent=agent, run_kind="invocation", status="running",
            caller_subject_hash=subject.subject_hash, caller_principal_type=subject.principal_type,
            caller_principal_id=subject.principal_id, write_token="write-test-only",
            interaction_token_hash=hash_token("test-only"),
            interaction_token_expires_at=timezone.now() + timedelta(minutes=5))
        return agent, run

    def setUp(self):
        row = provision_owner(email="owner@example.test", password=PASSWORD)
        self.owner, self.tenant = row.owner, row.tenant
        self.dataset = Dataset.objects.create(tenant=row.tenant, project=row.project,
            name="Images", created_by=row.owner)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        config = override_settings(NEXUS_DATASET_STORAGE_ROOT=self.temp.name,
            NEXUS_DATASET_STORAGE_BACKEND="local", MEDIA_ROOT=self.temp.name)
        config.enable()
        self.addCleanup(config.disable)
        self.client = APIClient()
        self.token = Token.objects.create(user=row.owner)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.token.key)
        self.headers = {"HTTP_X_NEXUS_TENANT": str(row.tenant.pk),
                        "HTTP_X_NEXUS_PROJECT": str(row.project.pk)}
        self.push = f"/api/v1/datasets/{self.dataset.pk}/push/"

    def media_payload(self, response):
        return response.json()

    def upload(self, name="sample.png", data=None, mime="image/png", **extra):
        return self.client.post(self.push, {"file": SimpleUploadedFile(name, raster() if data is None else data, content_type=mime),
            "rights_confirmed": "true", **extra}, format="multipart", **self.headers)
