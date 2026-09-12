"""Real concurrent transfer guards; limits come from the host distribution."""
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections
from django.test import override_settings

from apps.datasets.models import Dataset, DatasetFileIndex, DatasetQuota
from apps.datasets.services import create_dataset_file_from_upload
from apps.datasets.transfers import reserve_write, reserve_export


class DatasetTransferConcurrencyGuards:
    def test_eight_concurrent_saves_keep_exact_counters(self):
        barrier = threading.Barrier(4)
        def save(_):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                data = Dataset.objects.get(pk=self.dataset.pk)
                return create_dataset_file_from_upload(dataset=data, uploaded_file=SimpleUploadedFile("six", b"123456"), uploaded_by=None).id
            finally:
                close_old_connections()
        with tempfile.TemporaryDirectory() as root, override_settings(NEXUS_DATASET_STORAGE_ROOT=root):
            with ThreadPoolExecutor(max_workers=4) as pool:
                ids = list(pool.map(save, range(8)))
        self.dataset.refresh_from_db()
        self.assertEqual(len(set(ids)), 8)
        self.assertEqual((self.dataset.file_count, self.dataset.size_bytes), (8, 48))
        self.assertEqual(DatasetFileIndex.objects.filter(dataset=self.dataset).count(), 8)

    def test_tenant_storage_reservations_include_other_collections(self):
        other = self.other_collection()
        self.storage_limit_bytes(10)
        reserve_write(self.dataset, 6, "first")
        PlanQuotaExceeded = self.capacity_error()
        with self.assertRaises(PlanQuotaExceeded):
            reserve_write(other, 6, "second")

    def test_concurrent_6_plus_6_cannot_exceed_collection_quota_10(self):
        DatasetQuota.objects.create(tenant=self.tenant, dataset=self.dataset, max_size_bytes=10, raw_value="10B")
        barrier = threading.Barrier(2)
        def save(_):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                data = Dataset.objects.get(pk=self.dataset.pk)
                create_dataset_file_from_upload(dataset=data, uploaded_file=SimpleUploadedFile("six", b"123456"), uploaded_by=None)
                return "saved"
            except Exception as exc:
                return type(exc).__name__
            finally:
                close_old_connections()
        with tempfile.TemporaryDirectory() as root, override_settings(NEXUS_DATASET_STORAGE_ROOT=root):
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(save, range(2)))
        self.assertEqual(sorted(outcomes), ["DatasetQuotaExceeded", "saved"])
        self.dataset.refresh_from_db()
        self.assertEqual((self.dataset.file_count, self.dataset.size_bytes), (1, 6))

    def test_concurrent_export_reservations_enforce_total_capacity(self):
        barrier = threading.Barrier(4)
        def reserve(_):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                reserve_export(Dataset.objects.get(pk=self.dataset.pk), 1024 ** 3, uuid.uuid4().hex)
                return "reserved"
            except Exception as exc:
                return type(exc).__name__
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(reserve, range(4)))
        self.assertEqual(outcomes.count("reserved"), 2, outcomes)
        self.assertEqual(outcomes.count(self.capacity_error().__name__), 2, outcomes)
