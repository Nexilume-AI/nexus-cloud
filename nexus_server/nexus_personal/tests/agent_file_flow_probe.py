"""Original chunk race and real SDK HTTP roundtrip on Personal's committed file DB."""
from django.db import connection
from django.test import TransactionTestCase
from tests.agent_file_guards import AgentFileCommittedGuards
from .test_agent_file_flow import PersonalAgentFileFixture


class PersonalAgentFileCommittedTests(PersonalAgentFileFixture, AgentFileCommittedGuards, TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "sqlite")
        self.assertNotEqual(connection.settings_dict["NAME"], ":memory:")
        self.assertEqual(connection.settings_dict["OPTIONS"]["transaction_mode"], "IMMEDIATE")
        super().setUp()
