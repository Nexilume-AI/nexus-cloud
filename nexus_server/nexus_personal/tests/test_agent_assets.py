"""Real HTTP + database + local snapshot archives, not live Agent execution."""
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.agents.models import Agent, AgentDisplayRun, AgentDisplayEvent, AgentMemoryItem, AgentOutputArtifact
from apps.audit.models import AuditLog
from apps.common.subjects import request_subject
from apps.datasets.models import Dataset, DatasetFile, DatasetImportJob
from apps.datasets.import_jobs import run_job
from apps.datasets.storage_backends import get_dataset_storage_backend
from apps.tenancy.models import Project
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


class PersonalAgentAssetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="archive-owner@example.test", password=PASSWORD)
        cls.token = Token.objects.create(user=cls.row.owner)
        context = {"tenant": cls.row.tenant, "project": cls.row.project, "created_by": cls.row.owner}
        cls.agent = Agent.objects.create(**context, name="Archive assistant")
        cls.dataset = Dataset.objects.create(**context, name="Archives")
        subject = request_subject(SimpleNamespace(user=cls.row.owner, tenant_id=str(cls.row.tenant_id), headers={}))
        cls.display_run = AgentDisplayRun.objects.create(tenant=cls.row.tenant, agent=cls.agent,
            status="completed", redaction_status="passed", run_kind="invocation",
            caller_principal_type="user", caller_principal_id=str(cls.row.owner_id),
            caller_subject_hash=subject.subject_hash, consumer_tenant=cls.row.tenant,
            consumer_project=cls.row.project, write_token="not-a-live-run-token")
        cls.event = AgentDisplayEvent.objects.create(tenant=cls.row.tenant, agent=cls.agent, run=cls.display_run,
            seq=1, event_type="TEXT_MESSAGE_CONTENT", payload_json={"text": "raw-private-marker"},
            redacted_payload_json={"text": "归档文本"})
        cls.memory = AgentMemoryItem.objects.create(agent=cls.agent, source_run=cls.display_run,
            content_text="Personal memory", consent_status="approved", license_status="internal")

    def setUp(self):
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.token.key)
        folder = TemporaryDirectory(prefix="personal-agent-archive-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        config = override_settings(NEXUS_DATASET_STORAGE_ROOT=folder.name, NEXUS_DATASET_SPOOL_MIN_FREE_BYTES=0)
        config.enable()
        self.addCleanup(config.disable)

    def post(self, kind, **data):
        return self.client.post(f"/api/v1/datasets/{self.dataset.pk}/agent-assets/{kind}/",
            {"agent_id": str(self.agent.pk), **data}, format="json")

    def capture(self, artifact):
        return self.post("artifact", artifact_id=str(artifact.pk))

    def enqueue_trace(self, key="archive-job"):
        return self.client.post(f"/api/v1/datasets/{self.dataset.pk}/imports/",
            {"kind": "trace", "inputs": {"agent_id": str(self.agent.pk), "run_id": str(self.display_run.pk)},
             "request_key": key}, format="json")

    def snapshot(self):
        buffer = io.BytesIO()
        Image.new("RGB", (3, 2), "blue").save(buffer, format="PNG")
        payload = buffer.getvalue()
        stored = get_dataset_storage_backend().save(tenant=self.row.tenant, dataset_id="run-snapshots",
            file_name="result.png", uploaded_file=SimpleUploadedFile("result.png", payload, content_type="image/png"))
        artifact = AgentOutputArtifact.objects.create(agent=self.agent, run=self.display_run,
            workspace_path="result.png", original_file_name="result.png", content_type="image/png",
            producer_step="display_image:fixture", scan_status="passed", policy_status="approved",
            license_status="internal", sha256=stored.sha256, size_bytes=stored.size_bytes,
            snapshot_status="ready", snapshot_storage_backend="local", snapshot_object_key=stored.object_key)
        return artifact, payload

    def test_trace_uses_only_redacted_events_and_records_audit(self):
        response = self.post("trace", run_id=str(self.display_run.pk))
        self.assertEqual(response.status_code, 201, response.data)
        file = DatasetFile.objects.get(pk=response.data["id"])
        content = (self.root / file.object_key).read_bytes()
        self.assertNotIn(b"raw-private-marker", content)
        self.assertEqual(json.loads(content)["payload"]["text"], "归档文本")
        self.assertTrue(AuditLog.objects.filter(action="datasets.agent_trace.export").exists())

    def test_memory_archive_is_real_jsonl_with_source_identity(self):
        response = self.post("memory", memory_item_ids=[str(self.memory.pk)])
        self.assertEqual(response.status_code, 201, response.data)
        file = DatasetFile.objects.get(pk=response.data["id"])
        row = json.loads((self.root / file.object_key).read_bytes())
        self.assertEqual(row["content_text"], "Personal memory")
        self.assertEqual(row["source_run_id"], str(self.display_run.pk))

    def test_image_snapshot_archive_download_and_version_preserve_bytes(self):
        artifact, payload = self.snapshot()
        response = self.capture(artifact)
        self.assertEqual(response.status_code, 201, response.data)
        file = DatasetFile.objects.get(pk=response.data["id"])
        self.assertEqual((self.root / file.object_key).read_bytes(), payload)
        self.assertEqual(file.sha256, hashlib.sha256(payload).hexdigest())
        self.assertNotEqual(file.object_key, artifact.snapshot_object_key)
        download = self.client.get(f"/api/v1/datasets/{self.dataset.pk}/files/{file.pk}/download/")
        self.assertEqual(download.status_code, 200)
        try:
            self.assertEqual(b"".join(download.streaming_content), payload)
        finally:
            download.close()
        version = self.client.post(f"/api/v1/datasets/{self.dataset.pk}/versions/", {}, format="json")
        self.assertEqual(version.status_code, 201, version.data)

    def test_foreign_run_cannot_export_trace_memory_or_image(self):
        artifact, _ = self.snapshot()
        for values in ({"caller_subject_hash": "f" * 64}, {"caller_principal_id": "other"},
                       {"consumer_project": Project.objects.create(tenant=self.row.tenant, name="Other")}):
            with self.subTest(values=list(values)):
                previous = {key: getattr(self.display_run, key) for key in values}
                AgentDisplayRun.objects.filter(pk=self.display_run.pk).update(**values)
                for response in (self.post("trace", run_id=str(self.display_run.pk)),
                    self.post("memory", memory_item_ids=[str(self.memory.pk)]), self.capture(artifact)):
                    self.assertEqual(response.status_code, 404, response.data)
                AgentDisplayRun.objects.filter(pk=self.display_run.pk).update(**previous)
        self.assertFalse(DatasetFile.objects.exists())

    def test_foreign_agent_owner_and_project_are_rejected_before_file_write(self):
        other = get_user_model().objects.create_user(username="other")
        for values in ({"created_by": other}, {"project": Project.objects.create(tenant=self.row.tenant, name="Other")}):
            old = {key: getattr(self.agent, key) for key in values}
            Agent.objects.filter(pk=self.agent.pk).update(**values)
            response = self.post("trace", run_id=str(self.display_run.pk))
            self.assertEqual(response.status_code, 404, response.data)
            Agent.objects.filter(pk=self.agent.pk).update(**old)
        self.assertFalse(DatasetFile.objects.exists())
        self.assertFalse(list(self.root.rglob("*.jsonl")))

    def test_incomplete_or_unapproved_sources_and_hash_mismatch_do_not_archive(self):
        artifact, _ = self.snapshot()
        for field, bad in (("scan_status", "failed"), ("policy_status", "blocked"),
                           ("snapshot_status", "pending"), ("sha256", "e" * 64)):
            old = getattr(artifact, field)
            AgentOutputArtifact.objects.filter(pk=artifact.pk).update(**{field: bad})
            response = self.capture(artifact)
            self.assertEqual(response.status_code, 400, response.data)
            self.assertFalse(DatasetFile.objects.exists())
            AgentOutputArtifact.objects.filter(pk=artifact.pk).update(**{field: old})
        AgentDisplayRun.objects.filter(pk=self.display_run.pk).update(status="running")
        self.assertEqual(self.post("trace", run_id=str(self.display_run.pk)).status_code, 400)
        self.assertEqual(self.capture(artifact).status_code, 400)
        self.assertEqual(len([path for path in self.root.rglob("*") if path.is_file()]), 1)

    def test_real_background_import_worker_commits_once_and_replay_returns_existing_job(self):
        response = self.enqueue_trace()
        self.assertEqual(response.status_code, 202, response.data)
        job_id = response.data["id"]
        self.assertEqual(self.enqueue_trace().data["id"], job_id)
        # Invoke the actual DB worker, not a substitute runner or broker.
        run_job(job_id)
        job = DatasetImportJob.objects.get(pk=job_id)
        self.assertEqual(job.state, "completed", job.error_code)
        self.assertEqual(job.attempts, 1)
        self.assertEqual(json.loads((self.root / job.file.object_key).read_bytes())["payload"]["text"], "归档文本")
        run_job(job_id)
        replay = self.enqueue_trace()
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(replay.data["file_id"], str(job.file_id))
        self.assertEqual(DatasetFile.objects.count(), 1)
        self.assertEqual(DatasetImportJob.objects.count(), 1)

    def test_worker_rechecks_source_after_enqueue_and_can_retry_when_restored(self):
        response = self.enqueue_trace()
        self.assertEqual(response.status_code, 202, response.data)
        job_id = response.data["id"]
        AgentDisplayRun.objects.filter(pk=self.display_run.pk).update(caller_subject_hash="f" * 64)
        run_job(job_id)
        job = DatasetImportJob.objects.get(pk=job_id)
        self.assertEqual(job.state, "failed")
        self.assertEqual(job.error_code, "ACCESS_REVOKED_OR_SOURCE_UNAVAILABLE")
        self.assertFalse(DatasetFile.objects.exists())
        AgentDisplayRun.objects.filter(pk=self.display_run.pk).update(caller_subject_hash=self.display_run.caller_subject_hash)
        retry = self.client.post(f"/api/v1/datasets/{self.dataset.pk}/imports/{job_id}/", {"action": "retry"}, format="json")
        self.assertEqual(retry.status_code, 200, retry.data)
        run_job(job_id)
        job.refresh_from_db()
        self.assertEqual(job.state, "completed", job.error_code)
        self.assertEqual(job.attempts, 2)

    def test_archiving_obeys_real_file_capacity_without_partial_output(self):
        from .test_data_admission import LIMITS
        with override_settings(NEXUS_PERSONAL_DATA_LIMITS={**LIMITS, "data.files": 0}):
            response = self.post("trace", run_id=str(self.display_run.pk))
        self.assertEqual(response.status_code, 409, response.data)
        self.assertFalse(DatasetFile.objects.exists())
        self.assertFalse([path for path in self.root.rglob("*") if path.is_file()])
