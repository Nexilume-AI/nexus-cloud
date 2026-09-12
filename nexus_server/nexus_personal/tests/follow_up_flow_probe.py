"""Actual queue worker races and SDK background receiver on committed SQLite."""
from django.db import connection
from django.test import TransactionTestCase
from tests.follow_up_guards import FollowUpCommittedGuards
from .test_follow_up_flow import PersonalFollowUpFixture


class PersonalFollowUpCommittedTests(PersonalFollowUpFixture, FollowUpCommittedGuards, TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "sqlite")
        self.assertNotEqual(connection.settings_dict["NAME"], ":memory:")
        self.assertEqual(connection.settings_dict["OPTIONS"]["transaction_mode"], "IMMEDIATE")
        super().setUp()
