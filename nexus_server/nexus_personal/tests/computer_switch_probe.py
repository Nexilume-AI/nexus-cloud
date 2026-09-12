"""Two actual authenticated tabs race on Personal's committed file SQLite host."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import connections, connection
from django.test import TransactionTestCase
from rest_framework.test import APIClient
from . import test_run_computer_switch as fixtures


class PersonalComputerSwitchRaceTests(TransactionTestCase):
    setUp = fixtures.PersonalRunComputerSwitchTests.setUp
    setup_switch_state = fixtures.PersonalRunComputerSwitchTests.setup_switch_state
    _headers = fixtures.PersonalRunComputerSwitchTests._headers
    _display_headers = fixtures.PersonalRunComputerSwitchTests._display_headers
    private_payload = fixtures.PersonalRunComputerSwitchTests.private_payload
    authenticate_private_caller = fixtures.PersonalRunComputerSwitchTests.authenticate_private_caller
    create_private_runtime = fixtures.PersonalRunComputerSwitchTests.create_private_runtime
    create_switch_binding = fixtures.PersonalRunComputerSwitchTests.create_switch_binding
    create_switch_connection = fixtures.PersonalRunComputerSwitchTests.create_switch_connection
    computer = fixtures.PersonalRunComputerSwitchTests.computer

    def test_two_authenticated_tabs_cannot_replace_one_revision_twice(self):
        self.assertEqual(connection.vendor, "sqlite")
        self.assertNotEqual(connection.settings_dict["NAME"], ":memory:")
        self.assertEqual(connection.settings_dict["OPTIONS"]["transaction_mode"], "IMMEDIATE")
        barrier = Barrier(2)

        def change():
            try:
                client = APIClient(enforce_csrf_checks=True)
                client.cookies.update(self.client.cookies)
                barrier.wait(timeout=10)
                return client.post(f"/api/v1/agent-runs/{self.run.pk}/computer/",
                    {"connection_id": str(self.new.pk), "expected_revision": 0},
                    format="json", **self.headers).status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: change(), range(2)))
        self.assertEqual(sorted(results), [200, 409])
        self.run.refresh_from_db()
        self.assertEqual(self.run.computer_revision, 1)
        self.assertEqual(self.run.computer_attachments.count(), 2)
        self.assertEqual(self.run.computer_binding.connection_id, self.new.pk)
        self.binding.refresh_from_db()
        self.assertTrue(self.binding.is_default)
