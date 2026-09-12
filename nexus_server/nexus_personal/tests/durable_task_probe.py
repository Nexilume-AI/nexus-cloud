"""Actual eight-worker claim contention using Personal's committed file database."""
from django.db import connection
from django.test import TransactionTestCase
from tests.durable_task_guards import DurableClaimGuards
from .test_durable_tasks import PersonalDurableFixture


class PersonalDurableClaimsTests(PersonalDurableFixture, DurableClaimGuards, TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "sqlite")
        self.assertNotEqual(connection.settings_dict["NAME"], ":memory:")
        self.assertEqual(connection.settings_dict["OPTIONS"]["transaction_mode"], "IMMEDIATE")
        super().setUp()
