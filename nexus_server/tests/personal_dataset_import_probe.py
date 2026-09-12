"""Import concurrency inside the existing installed PostgreSQL acceptance.

The owning initialization fixture creates and removes the random database.
This probe starts no service and never flushes or drops database objects.
"""
import re
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from uuid import uuid4

from django.db import connection
from django.test import override_settings

from apps.agents.models import Agent, AgentDisplayRun, AgentDisplayEvent
from apps.common.subjects import request_subject
from apps.datasets.import_jobs import enqueue
from apps.datasets.models import Dataset, DatasetFile
from nexus_personal.models import PersonalInstallation
from tests.dataset_import_concurrency_guards import DatasetImportConcurrencyGuards


class PersonalDatasetImportConcurrencyTests(DatasetImportConcurrencyGuards, unittest.TestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql")
        self.assertFalse(connection.in_atomic_block)
        self.assertTrue(connection.get_autocommit())
        self.assertRegex(connection.settings_dict["NAME"], r"^nexus_personal_[0-9a-f]{32}$")
        # Preserve the original global exactly-one-file assertion. This probe
        # must never pass by narrowing that assertion to its own collection.
        self.assertEqual(DatasetFile.objects.count(), 0)
        row = PersonalInstallation.objects.select_related("owner", "tenant", "project").get(slot=1)
        self.assertTrue(row.owner.is_active)
        self.assertFalse(row.owner.is_superuser)
        self.temp = TemporaryDirectory(prefix="nexus-import-guard-")
        self.addCleanup(self.temp.cleanup)
        settings = override_settings(NEXUS_DATASET_STORAGE_BACKEND="local",
            NEXUS_DATASET_STORAGE_ROOT=self.temp.name,
            NEXUS_DATASET_SPOOL_MIN_FREE_BYTES=512 * 1024**2)
        settings.enable()
        self.addCleanup(settings.disable)
        self.request = SimpleNamespace(user=row.owner, tenant_id=str(row.tenant.pk),
            project_id=str(row.project.pk), headers={}, query_params={}, META={}, method="POST")
        subject = request_subject(self.request)
        key = "import-guard-" + uuid4().hex
        self.dataset = Dataset.objects.create(tenant=row.tenant, project=row.project,
            name=key, created_by=row.owner)
        self.agent = Agent.objects.create(tenant=row.tenant, project=row.project,
            name=key, created_by=row.owner)
        self.run = AgentDisplayRun.objects.create(tenant=row.tenant, consumer_project=row.project,
            consumer_tenant=row.tenant, agent=self.agent,
            caller_subject_hash=subject.subject_hash, caller_principal_type=subject.principal_type,
            caller_principal_id=subject.principal_id, status="completed", redaction_status="passed")
        AgentDisplayEvent.objects.create(tenant=row.tenant, agent=self.agent, run=self.run,
            seq=1, event_type="STEP_STARTED", payload_json={"stepName": "safe"},
            redacted_payload_json={"stepName": "safe"})
        self.inputs = {"agent_id": str(self.agent.pk), "run_id": str(self.run.pk)}

    def new_job(self, key="first"):
        return enqueue(self.request, self.dataset.pk, "trace", self.inputs, key)


def run(expected_database):
    if (not re.fullmatch(r"nexus_personal_[0-9a-f]{32}", expected_database)
            or connection.vendor != "postgresql"
            or connection.settings_dict["NAME"] != expected_database
            or connection.in_atomic_block or not connection.get_autocommit()):
        raise AssertionError("Explicit committed fixture-owned PostgreSQL database required")
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PersonalDatasetImportConcurrencyTests)
    assert suite.countTestCases() == 2
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    assert result.wasSuccessful() and not result.skipped
    return {"scope": "personal-dataset-import-postgres-guards", "tests_run": result.testsRun,
            "skipped": len(result.skipped), "vendor": connection.vendor}
