"""Actual personal owner authentication and export ledger with common HTTP guards."""
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.agents.models import AgentDisplayRun
from apps.common.subjects import request_subject
from apps.datasets.models import Dataset
from apps.datasets.services import create_dataset_file_from_upload
from nexus_personal.models import PersonalDataExportUsage
from nexus_personal.services import provision_owner
from tests.dataset_transfer_http_guards import DatasetTransferHTTPGuards
from .test_installation import PASSWORD


class PersonalDatasetTransferTests(DatasetTransferHTTPGuards, TestCase):
    def setUp(self):
        row = provision_owner(email="owner@example.test", password=PASSWORD)
        self.user, self.tenant = row.owner, row.tenant
        self.other = get_user_model().objects.create_user(username="download-other")
        self.dataset = Dataset.objects.create(tenant=row.tenant, project=row.project,
            name="Transfers", created_by=row.owner)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        config = override_settings(NEXUS_DATASET_STORAGE_ROOT=self.temp.name,
            NEXUS_DATASET_STORAGE_BACKEND="local")
        config.enable()
        self.addCleanup(config.disable)
        self.file = self.save(b"0123456789")
        self.path = f"/api/v1/datasets/{self.dataset.id}/files/{self.file.id}/download/"
        self.client = APIClient()
        self.authenticate_transfer_client(self.client, self.user)
        self.headers = {"HTTP_X_NEXUS_TENANT": str(row.tenant.pk),
                        "HTTP_X_NEXUS_PROJECT": str(row.project.pk)}

    def save(self, content):
        return create_dataset_file_from_upload(dataset=self.dataset, uploaded_by=self.user,
            uploaded_file=SimpleUploadedFile("data.bin", content))

    def export_usage(self):
        return PersonalDataExportUsage.objects.all()

    def transfer_payload(self, response):
        return response.json()

    def authenticate_transfer_client(self, client, user):
        if user is None:
            client.credentials()
        else:
            token, _ = Token.objects.get_or_create(user=user)
            client.credentials(HTTP_AUTHORIZATION="Bearer " + token.key)

    def revoke_transfer_access(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

    def artifact_request(self):
        return SimpleNamespace(user=self.user, tenant_id=str(self.tenant.pk),
            project_id=str(self.dataset.project_id), headers={}, META={})

    def artifact_run_fixture(self, agent):
        subject = request_subject(self.artifact_request())
        return AgentDisplayRun.objects.create(tenant=self.tenant, consumer_tenant=self.tenant,
            consumer_project=self.dataset.project, agent=agent, run_kind="invocation", status="completed",
            caller_subject_hash=subject.subject_hash, caller_principal_type=subject.principal_type,
            caller_principal_id=subject.principal_id)
