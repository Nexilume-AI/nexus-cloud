"""Shared file flows with actual owner session/CSRF and private local object storage."""
import tempfile
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from apps.accounts.models import AccountProfile
from apps.agents.models import AgentFileTransfer
from tests.agent_file_guards import FileArgumentGuards, AgentFileHelpers, AgentFileHTTPGuards
from . import test_private_run_flow as fixtures


class PersonalFileArgumentTests(FileArgumentGuards, SimpleTestCase):
    pass


class PersonalAgentFileFixture(AgentFileHelpers):
    _headers = fixtures.PersonalPrivateRunFlowTests._headers
    _display_headers = fixtures.PersonalPrivateRunFlowTests._display_headers
    private_payload = fixtures.PersonalPrivateRunFlowTests.private_payload
    file_payload = fixtures.PersonalPrivateRunFlowTests.private_payload
    authenticate_private_caller = fixtures.PersonalPrivateRunFlowTests.authenticate_private_caller
    create_private_runtime = fixtures.PersonalPrivateRunFlowTests.create_private_runtime

    def setUp(self):
        fixtures.PersonalPrivateRunFlowTests.setUp(self)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        config = override_settings(NEXUS_DATASET_STORAGE_ROOT=folder.name,
                                   NEXUS_DATASET_STORAGE_BACKEND="local")
        config.enable()
        self.addCleanup(config.disable)
        self.agent.repo_metadata = {"tools": [{"name": "inspect", "input_schema": {
            "type": "object", "properties": {"message": {"type": "string"},
                "files": FileArgumentGuards.file_schema()["properties"]["files"]},
            "required": ["message"]}}]}
        self.agent.save()

    def assert_other_file_caller_denied(self, row, path, headers):
        other = get_user_model().objects.create_user(username="foreign-file-reader")
        AccountProfile.objects.create(user=other, tenant_id=str(self.tenant.pk),
            project_id=str(self.project_a.pk), status="active")
        peer = APIClient()
        peer.credentials(HTTP_AUTHORIZATION="Bearer " + Token.objects.create(user=other).key)
        self.assertEqual(peer.get(path, **headers).status_code, 401)
        self.assertEqual(peer.get(f"/api/v1/agent-files/{row.pk}/", **self._headers()).status_code, 401)
        row.refresh_from_db()
        self.assertIsNotNone(row.run_id)

    def assert_other_file_project_denied(self, row):
        self.assertEqual(self.client.get(f"/api/v1/agent-files/{row.pk}/",
            **{**self._headers(), "HTTP_X_NEXUS_PROJECT": "foreign-project"}).status_code, 403)


class PersonalAgentFileFlowTests(PersonalAgentFileFixture, AgentFileHTTPGuards, TransactionTestCase):
    def test_upload_creation_requires_owner_and_csrf(self):
        body = {"agent_id": str(self.agent.pk), "name": "private.bin", "size_bytes": 1}
        self.assertEqual(self.client.post("/api/v1/agent-files/", body, format="json").status_code, 403)
        self.assertEqual(APIClient().post("/api/v1/agent-files/", body, format="json").status_code, 401)
        self.assertFalse(AgentFileTransfer.objects.exists())

    def test_foreign_scope_or_missing_csrf_cannot_write_or_cancel_parts(self):
        info = self.create(1)
        path = f"/api/v1/agent-files/{info['file_id']}/"
        self.assertEqual(self.client.put(path, b"x", content_type="application/octet-stream",
            HTTP_X_NEXUS_UPLOAD_OFFSET="0").status_code, 403)
        self.assertEqual(self.client.delete(path).status_code, 403)
        self.assertEqual(self.client.delete(path,
            **{**self._headers(), "HTTP_X_NEXUS_PROJECT": "foreign-project"}).status_code, 403)
        row = AgentFileTransfer.objects.get(pk=info["file_id"])
        self.assertEqual(row.received_bytes, 0)
        self.assertEqual(row.parts.count(), 0)
