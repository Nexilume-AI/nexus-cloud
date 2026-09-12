"""Explicit PostgreSQL cleanup/lock checks; never pretend SQLite tests the lock."""
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import mock
from django.db import connection, close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient
from apps.agents.python_build_cleanup import build_execution_lock, cleanup_deleted_builds
from apps.audit.models import AuditLog
from tests.python_build_guards import DIGEST
from tests.python_build_deletion_guards import PythonBuildCleanupGuards
from .test_python_build_deletion import PersonalBuildDeletionFixture
from .test_installation import PASSWORD


class PostgresDeletionFixture(PersonalBuildDeletionFixture):
    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql", "This probe requires its owned PostgreSQL database")
        super().setUp()


@override_settings(NEXUS_AGENT_PYTHON_BUILDS_ENABLED=True, NEXUS_AGENT_PYTHON_BASE_IMAGE=DIGEST,
                   NEXUS_AGENT_RUNTIME_HOST_ID="personal-build-delete-test")
class PersonalBuildCleanupTests(PostgresDeletionFixture, PythonBuildCleanupGuards, TestCase):
    pass


@override_settings(NEXUS_AGENT_PYTHON_BUILDS_ENABLED=True, NEXUS_AGENT_PYTHON_BASE_IMAGE=DIGEST,
                   NEXUS_AGENT_RUNTIME_HOST_ID="personal-build-delete-test")
class PersonalBuildDeletionConcurrencyTests(PostgresDeletionFixture, TransactionTestCase):
    def test_duplicate_delete_has_one_winner_and_one_audit(self):
        build = self.failed()
        barrier = threading.Barrier(2)

        def remove(_):
            close_old_connections()
            try:
                client = APIClient(enforce_csrf_checks=True)
                self.assertTrue(client.login(username=self.owner.username, password=PASSWORD))
                client.get("/api/v1/public/bootstrap/")
                token = client.cookies["csrftoken"].value
                barrier.wait(timeout=10)
                return client.delete(f"{self.url}{build.pk}/", HTTP_X_CSRFTOKEN=token).status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as workers:
            self.assertEqual(sorted(workers.map(remove, range(2))), [200, 404])
        self.assertEqual(AuditLog.objects.filter(action="agents.runtime.python.delete_failed").count(), 1)

    def test_cleanup_waits_for_live_execution_lock(self):
        build = self.failed()
        self.delete(build)
        with build_execution_lock(build.pk) as acquired:
            self.assertTrue(acquired)

            def cleanup():
                close_old_connections()
                try:
                    return cleanup_deleted_builds()
                finally:
                    close_old_connections()

            with ThreadPoolExecutor(max_workers=1) as workers, mock.patch("apps.agents.python_build_cleanup.docker") as docker:
                self.assertEqual(workers.submit(cleanup).result(timeout=10), 0)
                docker.assert_not_called()
