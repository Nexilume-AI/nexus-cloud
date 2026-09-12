"""Real row-lock tests inside the existing installed, disposable PostgreSQL DB."""
from decimal import Decimal
import re
import unittest
from uuid import uuid4

from django.db import connection
from django.test import override_settings

from apps.datasets.models import Dataset, DatasetFile, DatasetTransfer
from nexus_personal.models import PersonalInstallation
from nexus_personal.resource_limits import PersonalCapacityExceeded
from tests.dataset_transfer_concurrency_guards import DatasetTransferConcurrencyGuards


def require_fixture(expected_database):
    if (not re.fullmatch(r"nexus_personal_[0-9a-f]{32}", expected_database)
            or connection.vendor != "postgresql"
            or connection.settings_dict["NAME"] != expected_database
            or connection.in_atomic_block or not connection.get_autocommit()):
        raise AssertionError("Explicit committed fixture-owned PostgreSQL database required")


class PersonalDatasetTransferConcurrencyTests(DatasetTransferConcurrencyGuards, unittest.TestCase):
    def setUp(self):
        self.database_name = connection.settings_dict["NAME"]
        require_fixture(self.database_name)
        self.assertEqual(DatasetFile.objects.count(), 0)
        self.assertEqual(DatasetTransfer.objects.count(), 0)
        self.row = PersonalInstallation.objects.select_related("owner", "tenant", "project").get(slot=1)
        self.assertTrue(self.row.owner.is_active)
        self.assertFalse(self.row.owner.is_superuser)
        self.tenant = self.row.tenant
        self.fixture_ids = []
        self.addCleanup(self.clean_fixture_collections)
        self.limits = {"data.collections": 100, "data.files": 100, "data.storage_gb": 2,
                       "data.export_gb_per_30_days": 2}
        configuration = override_settings(NEXUS_PERSONAL_DATA_LIMITS=self.limits,
                                          NEXUS_DATASET_STORAGE_BACKEND="local")
        configuration.enable()
        self.addCleanup(configuration.disable)
        self.dataset = self.other_collection()

    def other_collection(self):
        dataset = Dataset.objects.create(tenant=self.tenant, project=self.row.project,
            name="transfer-guard-" + uuid4().hex, created_by=self.row.owner)
        self.fixture_ids.append(dataset.pk)
        return dataset

    def storage_limit_bytes(self, size):
        self.limits["data.storage_gb"] = str(Decimal(size) / Decimal(1024 ** 3))

    def capacity_error(self):
        return PersonalCapacityExceeded

    def clean_fixture_collections(self):
        require_fixture(self.database_name)
        # Delete only rows created by this testcase, never flush the installed
        # schema, the installation, or other probes' data. Files live in the
        # existing shared tests' TemporaryDirectory and are cleaned there.
        Dataset.objects.filter(pk__in=self.fixture_ids, tenant=self.tenant,
            project=self.row.project, created_by=self.row.owner).delete()
        self.assertFalse(Dataset.objects.filter(pk__in=self.fixture_ids).exists())


def run(expected_database):
    require_fixture(expected_database)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PersonalDatasetTransferConcurrencyTests)
    assert suite.countTestCases() == 4
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    assert result.wasSuccessful() and not result.skipped
    return {"scope": "personal-dataset-transfer-postgres-guards", "tests_run": result.testsRun,
            "skipped": len(result.skipped), "vendor": connection.vendor}
