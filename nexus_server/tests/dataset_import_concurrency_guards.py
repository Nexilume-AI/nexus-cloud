"""Unchanged real database concurrency assertions shared by both hosts."""
from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections
from apps.datasets.import_jobs import run_job
from apps.datasets.models import DatasetFile


class DatasetImportConcurrencyGuards:
    def test_duplicate_dispatch_commits_exactly_once(self):
        job = self.new_job()
        def work(_):
            close_old_connections()
            try:
                run_job(job.pk)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(work, range(8)))
        job.refresh_from_db()
        self.assertEqual(job.state, "completed", job.error_code)
        self.assertEqual(job.attempts, 1)
        self.assertEqual(DatasetFile.objects.count(), 1)

    def test_concurrent_enqueue_same_key(self):
        def work(_):
            close_old_connections()
            try:
                return self.new_job().pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=8) as executor:
            ids = list(executor.map(work, range(8)))
        self.assertEqual(len(set(ids)), 1)
