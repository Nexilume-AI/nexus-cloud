"""Shared import assertions; fixtures adapt only the response envelope."""
import io
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.core.management import call_command, CommandError
from django.utils import timezone

from apps.datasets.import_jobs import Attempt, LostLease, _attempt, enqueue, pending_jobs, run_job
from apps.datasets.models import DatasetFile, DatasetImportJob, DatasetTransfer
from apps.datasets.services import create_dataset_file_from_bytes


class DatasetImportQueueGuards:
    def test_incomplete_legacy_manifest_is_preserved_without_broken_downloads(self):
        from apps.datasets.models import DatasetVersion
        original = {"files": [{"file_name": "legacy", "size_bytes": 12, "storage_path": "private/path"}]}
        version = DatasetVersion.objects.create(tenant=self.tenant, dataset=self.dataset, version="legacy", file_count=1, snapshot_json=original)
        with self.assertRaises(CommandError):
            call_command("normalize_dataset_manifests", tenant=str(self.tenant.pk), stdout=io.StringIO())
        version.refresh_from_db()
        self.assertEqual(version.snapshot_json, original)
        response = self.client.get(f"/api/v1/datasets/{self.dataset.pk}/versions/{version.pk}/files/", **self.headers)
        self.assertEqual(response.status_code, 200, response.content)
        data = self.import_payload(response)
        self.assertEqual(data["warning_code"], "LEGACY_MANIFEST_INCOMPLETE")
        self.assertEqual(data["unavailable_entries"], 1)
        self.assertEqual(data["items"], [])
        self.assertNotIn("private/path", json.dumps(data))
        report = io.StringIO()
        with self.assertRaises(CommandError):
            call_command("verify_dataset_storage", tenant=str(self.tenant.pk), release_id=str(version.pk), stdout=report)
        self.assertEqual(json.loads(report.getvalue())["warning_code"], "LEGACY_MANIFEST_INCOMPLETE")

    def test_temporary_disk_guard_fails_without_committing_a_file(self):
        job = self.new_job()
        with mock.patch("shutil.disk_usage", return_value=SimpleNamespace(free=0)):
            run_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.state, "failed")
        self.assertFalse(DatasetFile.objects.exists())

    def test_broker_failure_and_dispatch_deduplication(self):
        from apps.datasets.tasks import dispatch_dataset_imports
        job = self.new_job()
        with mock.patch("apps.datasets.tasks.process_dataset_import.apply_async", side_effect=OSError("broker unavailable")):
            with self.assertRaises(OSError):
                dispatch_dataset_imports()
        self.assertIn(job.pk, pending_jobs())
        with mock.patch("apps.datasets.tasks.process_dataset_import.apply_async") as send:
            dispatch_dataset_imports()
            dispatch_dataset_imports()
            self.assertEqual(send.call_count, 1)
        self.assertNotIn(job.pk, pending_jobs())
        DatasetImportJob.objects.filter(pk=job.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertIn(job.pk, pending_jobs())
        run_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.state, "completed")

    def test_normalized_manifest_pages_and_direct_download(self):
        from apps.datasets.services import create_version, download_dataset_version_file
        for i in range(7):
            create_dataset_file_from_bytes(dataset=self.dataset, uploaded_by=self.user, file_name=f"{i}.txt", content=b"saved", content_type="text/plain")
        version = create_version(request=self.request, dataset_id=self.dataset.pk)
        self.assertEqual(version.entries.count(), 7)
        self.assertNotIn("files", version.snapshot_json)
        listing = self.import_payload(self.client.get(f"/api/v1/datasets/{self.dataset.pk}/versions/", {"limit": 2}, **self.headers))
        self.assertEqual(listing["items"][0]["snapshot_json"]["files"], [])
        self.assertTrue(listing["items"][0]["snapshot_json"]["paginated"])
        url = f"/api/v1/datasets/{self.dataset.pk}/versions/{version.pk}/files/"
        first = self.import_payload(self.client.get(url, {"limit": 3}, **self.headers))
        second = self.import_payload(self.client.get(url, {"limit": 3, "cursor": first["next_cursor"]}, **self.headers))
        self.assertEqual(first["total"], 7)
        self.assertFalse({row["file_id"] for row in first["items"]} & {row["file_id"] for row in second["items"]})
        self.assertNotIn("object_key", json.dumps(first))
        file = DatasetFile.objects.get(pk=first["items"][0]["file_id"])
        file.object_key = "mutable-row-changed"
        file.save()
        response = self.client.get(first["items"][0]["download_url"], **self.headers)
        self.assertEqual(b"".join(response.streaming_content), b"saved")

    def test_legacy_normalization_and_restore_verification(self):
        from apps.datasets.models import DatasetVersion
        from apps.datasets.storage_backends import resolve_local_object_key
        file = create_dataset_file_from_bytes(dataset=self.dataset, uploaded_by=self.user, file_name="restore.txt", content=b"restore", content_type="text/plain")
        version = DatasetVersion.objects.create(tenant=self.tenant, dataset=self.dataset, version="v1", file_count=1, size_bytes=7,
            snapshot_json={"release_notes": "preserved", "files": [{"file_id": str(file.pk), "file_name": file.file_name,
                "object_key": file.object_key, "storage_backend": "local", "size_bytes": 7, "sha256": file.sha256}]})
        call_command("normalize_dataset_manifests", tenant=str(self.tenant.pk), stdout=io.StringIO())
        call_command("normalize_dataset_manifests", tenant=str(self.tenant.pk), stdout=io.StringIO())
        version.refresh_from_db()
        self.assertEqual(version.entries.count(), 1)
        self.assertEqual(version.snapshot_json["release_notes"], "preserved")
        report = io.StringIO()
        call_command("verify_dataset_storage", tenant=str(self.tenant.pk), release_id=str(version.pk), stdout=report)
        self.assertEqual(json.loads(report.getvalue())["checked"], 1)
        with mock.patch("apps.datasets.operations.get_dataset_storage_backend") as backend:
            backend.return_value.open.return_value = io.BytesIO(b"damaged")
            with self.assertRaises(CommandError):
                call_command("verify_dataset_storage", tenant=str(self.tenant.pk), release_id=str(version.pk), stdout=io.StringIO())

    def test_deletion_rejects_active_imports(self):
        self.new_job()
        response = self.client.delete(f"/api/v1/datasets/{self.dataset.pk}/", **self.headers)
        self.assertEqual(response.status_code, 400)

    def test_operations_alerts_recover_and_preserve_customization(self):
        from apps.datasets.operations import operational_metrics, ensure_operational_alerts
        from apps.metrics.models import AlertRule, AlertEvent
        evaluate_alert_rules, capture_metric_snapshot = self.monitoring_operations()
        job = self.new_job()
        DatasetImportJob.objects.filter(pk=job.pk).update(created_at=timezone.now() - timedelta(minutes=10))
        ensure_operational_alerts(self.tenant)
        self.assertGreater(operational_metrics(self.tenant)["queue_age_seconds"], 300)
        evaluate_alert_rules(metric_prefix="data_assets.")
        self.assertTrue(AlertEvent.objects.filter(tenant=self.tenant, metric="data_assets.queue_age_seconds", status="firing").exists())
        run_job(job.pk)
        evaluate_alert_rules(metric_prefix="data_assets.")
        self.assertFalse(AlertEvent.objects.filter(tenant=self.tenant, metric="data_assets.queue_age_seconds", status="firing").exists())
        rule = AlertRule.objects.get(tenant=self.tenant, metric="data_assets.queue_age_seconds")
        rule.status, rule.threshold_value = "disabled", 999
        rule.save()
        ensure_operational_alerts(self.tenant)
        rule.refresh_from_db()
        self.assertEqual((rule.status, rule.threshold_value), ("disabled", 999))
        capture_metric_snapshot(tenant=self.tenant)
        snapshot = capture_metric_snapshot(tenant=self.tenant)
        self.assertNotIn("snapshot", snapshot.metrics_json)

    def test_persistent_enqueue_and_atomic_file_completion(self):
        response = self.client.post(self.url, {"kind": "trace", "inputs": self.inputs, "request_key": "persist"}, format="json", **self.headers)
        self.assertEqual(response.status_code, 202, response.content)
        job = DatasetImportJob.objects.get(pk=self.import_payload(response)["id"])
        self.assertEqual(job.state, "queued")
        self.assertFalse(DatasetFile.objects.exists())
        run_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.state, "completed", job.error_code)
        self.assertIsNotNone(job.file_id)
        self.assertEqual(job.bytes_processed, job.file.size_bytes)
        run_job(job.pk)
        self.assertEqual(DatasetFile.objects.count(), 1)
        replay = self.new_job("persist")
        self.assertEqual(replay.pk, job.pk)

    def test_conflicting_idempotency_key(self):
        self.new_job()
        from apps.datasets.import_jobs import ImportConflict
        with self.assertRaises(ImportConflict):
            enqueue(self.request, self.dataset.pk, "trace", {**self.inputs, "run_id": str(uuid.uuid4())}, "first")

    def test_current_source_and_account_authorization(self):
        job = self.new_job()
        self.user.is_active = False
        self.user.save()
        run_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.error_code, "ACCESS_REVOKED_OR_SOURCE_UNAVAILABLE")
        self.assertFalse(DatasetFile.objects.exists())

    def test_cancel_retry_and_worker_recovery(self):
        job = self.new_job()
        response = self.client.post(f"{self.url}{job.pk}/", {"action": "cancel"}, format="json", **self.headers)
        self.assertEqual(response.status_code, 200)
        run_job(job.pk)
        self.assertFalse(DatasetFile.objects.exists())
        response = self.client.post(f"{self.url}{job.pk}/", {"action": "retry"}, format="json", **self.headers)
        self.assertEqual(response.status_code, 200)
        DatasetImportJob.objects.filter(pk=job.pk).update(state="running", lease_id=uuid.uuid4(),
            lease_expires_at=timezone.now() - timedelta(seconds=1), attempts=1)
        self.assertIn(job.pk, pending_jobs())
        run_job(job.pk)
        job.refresh_from_db()
        self.assertEqual((job.state, job.attempts), ("completed", 2))

    def test_stale_worker_cannot_commit_file_or_counters(self):
        job = self.new_job()
        job.state, job.lease_id, job.lease_expires_at = "running", uuid.uuid4(), timezone.now() + timedelta(minutes=5)
        job.save()
        context = _attempt.set(Attempt(job))
        try:
            DatasetImportJob.objects.filter(pk=job.pk).update(lease_id=uuid.uuid4())
            with self.assertRaises(LostLease):
                create_dataset_file_from_bytes(dataset=self.dataset, uploaded_by=self.user, file_name="stale.txt", content=b"stale", content_type="text/plain")
        finally:
            _attempt.reset(context)
        self.dataset.refresh_from_db()
        self.assertEqual(self.dataset.file_count, 0)
        self.assertFalse(DatasetFile.objects.exists())
        self.assertFalse(DatasetTransfer.objects.filter(state="completed").exists())

    def test_errors_never_contain_secrets_and_source_gates_are_preserved(self):
        job = self.new_job()
        with mock.patch("apps.datasets.agent_asset_services.export_agent_trace_to_dataset", side_effect=RuntimeError("token=TOP-SECRET https://internal/private")):
            run_job(job.pk)
        response = self.client.get(f"{self.url}{job.pk}/", **self.headers)
        self.assertNotIn("TOP-SECRET", json.dumps(response.json()))
        self.assertEqual(self.import_payload(response)["error_code"], "IMPORT_FAILED")
        self.run.status = "running"
        self.run.save()
        blocked = self.new_job("blocked")
        run_job(blocked.pk)
        blocked.refresh_from_db()
        self.assertEqual(blocked.error_code, "SOURCE_NOT_READY")

    def test_keyset_pagination_and_cursor_scope(self):
        for key in range(5):
            self.new_job(str(key))
        first = self.import_payload(self.client.get(self.url, {"limit": 2}, **self.headers))
        second = self.import_payload(self.client.get(self.url, {"limit": 2, "cursor": first["next_cursor"]}, **self.headers))
        self.assertEqual(first["total"], 5)
        self.assertFalse(set(row["id"] for row in first["items"]) & set(row["id"] for row in second["items"]))
        response = self.client.get(self.url, {"limit": 2, "cursor": first["next_cursor"], "q": "changed"}, **self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(self.url, {"limit": 201}, **self.headers).status_code, 400)

    def test_collection_and_file_pages_use_global_totals(self):
        for i in range(5):
            create_dataset_file_from_bytes(dataset=self.dataset, uploaded_by=self.user, file_name=f"{i}.txt", content=b"abc", content_type="text/plain", metadata={"source_type": "agent_trace"})
        response = self.client.get(f"/api/v1/datasets/{self.dataset.pk}/pull/", {"limit": 2}, **self.headers)
        data = self.import_payload(response)
        self.assertEqual((len(data["files"]), data["total"], data["summary"]["trace"]), (2, 5, 5))
        response = self.client.get("/api/v1/datasets/", {"limit": 1}, **self.headers)
        self.assertEqual(self.import_payload(response)["summary"]["size_bytes"], 15)
