"""Actual personal file staging, integrity checks and immutable Run inputs."""
import hashlib
from datetime import timedelta
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import exceptions
from apps.agents import file_transfers as files
from apps.agents.models import Agent, AgentDisplayRun
from apps.common.subjects import request_subject
from apps.datasets.storage_backends import get_dataset_storage_backend
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


class PersonalAgentFileTransferTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="file-owner@example.test", password=PASSWORD)
        cls.agent = Agent.objects.create(tenant=cls.row.tenant, project=cls.row.project,
            created_by=cls.row.owner, name="Files", status="active")

    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        config = override_settings(NEXUS_DATASET_STORAGE_ROOT=folder.name)
        config.enable()
        self.addCleanup(config.disable)

    def request(self):
        return SimpleNamespace(user=self.row.owner, tenant_id=str(self.row.tenant_id),
            project_id=str(self.row.project_id), META={}, headers={}, query_params={})

    def run_record(self):
        return AgentDisplayRun.objects.create(tenant=self.row.tenant, consumer_tenant=self.row.tenant,
            consumer_project=self.row.project, agent=self.agent, run_kind="invocation", status="running",
            caller_subject_hash=request_subject(self.request()).subject_hash)

    def upload(self, content, **changes):
        data = {"agent_id": self.agent.pk, "name": "输入.txt", "content_type": "text/plain",
            "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(), **changes}
        row = files.create_transfer(request=self.request(), data=data)
        if content:
            row = files.put_part(row, offset=0, body=content, digest=hashlib.sha256(content).hexdigest())
        files.complete_transfer(row)
        self.assertTrue(files.process_one())
        row.refresh_from_db()
        return row

    def test_unicode_file_staged_checked_bound_and_read_from_real_storage(self):
        body = "真实文件输入\n".encode()
        row = self.upload(body)
        self.assertEqual(row.state, "ready")
        self.assertEqual(row.sha256, hashlib.sha256(body).hexdigest())
        self.assertEqual(files.owner_transfer(self.request(), row.pk).pk, row.pk)
        with get_dataset_storage_backend(row.storage_backend).open(object_key=row.object_key) as stream:
            self.assertEqual(stream.read(), body)
        descriptor = {"input_schema": {"properties": {"files": {"type": "array"}}}}
        prepared = files.prepare_files(request=self.request(), descriptor=descriptor, references=[str(row.pk)], agent_id=self.agent.pk)
        run = self.run_record()
        blocks = files.bind_files(run=run, rows=prepared, turn_index=1)
        self.assertEqual(blocks[0]["url"], f"/api/v1/agent-runs/{run.pk}/files/{row.pk}/download/")
        files.bind_files(run=run, rows=prepared, turn_index=2)
        row.refresh_from_db()
        self.assertEqual(row.turn_index, 1)
        self.assertEqual(row.run_id, run.pk)

    def test_integrity_failure_is_explicit_and_cannot_be_attached(self):
        row = self.upload(b"document", sha256="0" * 64)
        self.assertEqual(row.state, "failed")
        self.assertEqual(row.error_code, "FILE_FINALIZATION_FAILED")
        with self.assertRaises(exceptions.NotFound):
            files.prepare_files(request=self.request(), descriptor={"input_schema": {"properties": {"files": {"type": "array"}}}},
                references=[str(row.pk)], agent_id=self.agent.pk)

    def test_chunk_retry_preserves_bytes_and_conflicting_content_is_rejected(self):
        row = files.create_transfer(request=self.request(), data={"agent_id": self.agent.pk,
            "name": "chunk.txt", "size_bytes": 3})
        digest = hashlib.sha256(b"abc").hexdigest()
        files.put_part(row, offset=0, body=b"abc", digest=digest)
        files.put_part(row, offset=0, body=b"abc", digest=digest)
        row.refresh_from_db()
        self.assertEqual(row.received_bytes, 3)
        with self.assertRaises(files.FileConflict):
            files.put_part(row, offset=0, body=b"xyz", digest=hashlib.sha256(b"xyz").hexdigest())
        with self.assertRaises(exceptions.ValidationError):
            files.put_part(row, offset=0, body=b"abc", digest="0" * 64)

    def test_foreign_subject_expired_upload_and_cross_run_reuse_are_rejected(self):
        row = self.upload(b"private")
        original = row.caller_subject_hash
        row.caller_subject_hash = "foreign"
        row.save(update_fields=["caller_subject_hash"])
        with self.assertRaises(exceptions.NotFound):
            files.owner_transfer(self.request(), row.pk)
        row.caller_subject_hash = original
        row.expires_at = timezone.now() - timedelta(seconds=1)
        row.save()
        with self.assertRaises(exceptions.NotFound):
            files.owner_transfer(self.request(), row.pk)
        row.expires_at = timezone.now() + timedelta(hours=1)
        row.save()
        files.bind_files(run=self.run_record(), rows=[row])
        with self.assertRaises(files.FileConflict):
            files.bind_files(run=self.run_record(), rows=[row])
