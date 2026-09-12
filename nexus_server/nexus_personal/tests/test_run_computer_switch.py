"""Owner-authenticated Computer switching; metadata sessions are not live devices."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from apps.accounts.models import AccountProfile
from apps.agents.models import AgentComputerBinding
from apps.tenancy.models import Project
from apps.workspaces.models import WorkspaceConnection, ComputerRuntimeDevice
from tests.run_computer_switch_guards import RunComputerSwitchGuards
from . import test_private_run_flow as fixtures


class PersonalRunComputerSwitchTests(RunComputerSwitchGuards, TestCase):
    _headers = fixtures.PersonalPrivateRunFlowTests._headers
    _display_headers = fixtures.PersonalPrivateRunFlowTests._display_headers
    _create = fixtures.PersonalPrivateRunFlowTests._create
    private_payload = fixtures.PersonalPrivateRunFlowTests.private_payload
    authenticate_private_caller = fixtures.PersonalPrivateRunFlowTests.authenticate_private_caller
    create_private_runtime = fixtures.PersonalPrivateRunFlowTests.create_private_runtime

    def setUp(self):
        fixtures.PersonalPrivateRunFlowTests.setUp(self)
        self.setup_switch_state()

    def create_switch_binding(self, **fields):
        return AgentComputerBinding.objects.create(project=self.project_a, **fields)

    def create_switch_connection(self, **fields):
        return WorkspaceConnection.objects.create(created_by=self.caller_a, **fields)

    def test_switch_requires_owner_csrf_and_display_token(self):
        path = f"/api/v1/agent-runs/{self.run.pk}/computer/"
        body = {"connection_id": str(self.new.pk), "expected_revision": 0}
        self.assertEqual(self.switch(headers=self._headers()).status_code, 404)
        display = {"HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN": self.headers["HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN"]}
        self.assertEqual(self.client.post(path, body, format="json", **display).status_code, 403)
        self.assertEqual(APIClient().post(path, body, format="json", **self.headers).status_code, 401)
        other = get_user_model().objects.create_user(username="foreign-device-switcher")
        AccountProfile.objects.create(user=other, tenant_id=str(self.tenant.pk),
            project_id=str(self.project_a.pk), status="active")
        peer = APIClient()
        peer.credentials(HTTP_AUTHORIZATION="Bearer " + Token.objects.create(user=other).key)
        self.assertEqual(peer.post(path, body, format="json", **self.headers).status_code, 401)
        self.assertEqual(self.switch(headers={**self.headers, "HTTP_X_NEXUS_PROJECT": "foreign"}).status_code, 403)
        self.run.refresh_from_db()
        self.assertEqual(self.run.computer_revision, 0)
        self.assertEqual(self.run.computer_binding_id, self.binding.pk)

    def test_foreign_device_or_project_and_offline_are_rejected(self):
        self.new.owner_subject_hash = "another-caller"
        self.new.save(update_fields=["owner_subject_hash"])
        self.assertEqual(self.switch().status_code, 404)
        self.new.owner_subject_hash = self.subject.subject_hash
        self.new.project = Project.objects.create(tenant=self.tenant, name="Unrelated Project")
        self.new.save(update_fields=["owner_subject_hash", "project"])
        self.assertEqual(self.switch().status_code, 404)
        self.new.project = self.project_a
        self.new.save(update_fields=["project"])
        ComputerRuntimeDevice.objects.filter(connection=self.new).update(last_seen_at=None)
        self.assertEqual(self.switch().status_code, 503)
        self.run.refresh_from_db()
        self.assertEqual(self.run.computer_revision, 0)
        self.assertEqual(self.run.computer_binding_id, self.binding.pk)
