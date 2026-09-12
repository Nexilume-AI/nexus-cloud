"""Original protocol guards through real Personal owner auth and separate device requests.

Frames are submitted by the test; this is not an attached hardware or WSS E2E.
"""
from django.test import TestCase, override_settings
from apps.workspaces.models import WorkspaceConnection
from tests.computer_runtime_protocol_guards import ComputerRuntimeProtocolGuards
from . import test_computer_runtime_http as computer_fixture


@override_settings(NEXUS_PUBLIC_BASE_URL="https://cloud.example.test", NEXUS_LEGACY_SSH_ENABLED=False)
class PersonalComputerProtocolGuardsTests(ComputerRuntimeProtocolGuards, TestCase):
    @classmethod
    def setUpTestData(cls):
        computer_fixture.PersonalComputerRuntimeTests.setUpTestData.__func__(cls)

    def setUp(self):
        computer_fixture.PersonalComputerRuntimeTests.setUp(self)
        self.assertFalse(self.row.owner.is_superuser)
        self.headers = {"HTTP_X_NEXUS_TENANT": str(self.row.tenant_id)}

    def runtime_request(self, method, path, *args, **kwargs):
        # Machine signatures/tickets/uploads must not inherit the owner bearer.
        client = self.peer if path.startswith("/api/v1/computer-runtime/v1/") else self.client
        return getattr(client, method)(path, *args, **kwargs)

    def runtime_payload(self, response):
        return response.json()

    def test_legacy_ssh_creation_is_not_exposed(self):
        before = WorkspaceConnection.objects.count()
        response = self.client.post(
            "/api/v1/workspace-connections/",
            {"name": "legacy", "ssh_host": "127.0.0.1", "ssh_user": "caller",
             "auth_mode": "password", "password": "not-accepted"},
            format="json", **self.headers,
        )
        self.assertEqual(response.status_code, 405)
        self.assertEqual(WorkspaceConnection.objects.count(), before)
