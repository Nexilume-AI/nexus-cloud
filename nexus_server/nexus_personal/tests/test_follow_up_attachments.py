"""Personal attachment delivery with real storage, owner session and CSRF."""
import tempfile
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from apps.accounts.models import AccountProfile
from apps.agents.models import AgentRunFollowUp
from tests.agent_file_guards import AgentFileHelpers
from tests.follow_up_attachment_guards import FollowUpAttachmentHelpers, FollowUpAttachmentGuards
from .test_follow_up_flow import PersonalFollowUpFixture


class PersonalFollowUpAttachmentTests(FollowUpAttachmentHelpers, PersonalFollowUpFixture,
                                     AgentFileHelpers, FollowUpAttachmentGuards, TransactionTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        config = override_settings(MEDIA_ROOT=folder.name, NEXUS_MEDIA_STORAGE_ROOT=folder.name,
            NEXUS_DATASET_STORAGE_ROOT=folder.name, NEXUS_DATASET_STORAGE_BACKEND="local")
        config.enable()
        self.addCleanup(config.disable)
        PersonalFollowUpFixture.setUp(self)

    def attachment_payload(self, response):
        return response.json()

    file_payload = attachment_payload

    def test_idempotent_receipt_cannot_replace_accepted_image(self):
        image = self.image()
        self.assertEqual(self.send(image).status_code, 202)
        original = AgentRunFollowUp.objects.get().attachment_manifest
        self.assertEqual(self.send(self.image()).status_code, 409)
        self.assertEqual(AgentRunFollowUp.objects.count(), 1)
        self.assertEqual(AgentRunFollowUp.objects.get().attachment_manifest, original)

    def test_attachment_submission_requires_owner_csrf_and_fixed_project(self):
        image, file = self.image(), self.upload(100)
        body = {"mode": "queue", "content": "private attachments", "turn_index": 1,
            "idempotency_key": "denied", "attachments": [{"asset_id": image}], "files": [str(file.pk)]}
        other = get_user_model().objects.create_user(username="foreign-attachment-owner")
        AccountProfile.objects.create(user=other, tenant_id=str(self.tenant.pk), project_id=str(self.project_a.pk), status="active")
        peer = APIClient()
        peer.credentials(HTTP_AUTHORIZATION="Bearer " + Token.objects.create(user=other).key)
        self.assertEqual(peer.post(self.path, body, format="json", **self.headers).status_code, 401)
        no_csrf = {key: value for key, value in self.headers.items() if key != "HTTP_X_CSRFTOKEN"}
        self.assertEqual(self.client.post(self.path, body, format="json", **no_csrf).status_code, 403)
        self.assertEqual(self.client.post(self.path, body, format="json",
            **{**self.headers, "HTTP_X_NEXUS_PROJECT": "foreign-project"}).status_code, 403)
        self.assertFalse(AgentRunFollowUp.objects.exists())
        self.assertFalse(self.task.run.display_assets.exists())
        file.refresh_from_db()
        self.assertIsNone(file.run_id)

    def test_another_owned_run_cannot_consume_an_already_bound_file(self):
        file = self.upload(100)
        first = self.send(file=file)
        self.assertEqual(first.status_code, 202, first.content)
        response = self._create("Separate conversation")
        self.assertEqual(response.status_code, 202, response.content)
        other_run = self.follow_payload(response)["run_id"]
        self.assertNotEqual(other_run, self.run_id)
        headers = self._display_headers(other_run)
        denied = self.client.post(f"/api/v1/agent-runs/{other_run}/follow-ups/",
            {"mode": "queue", "content": "reuse file", "turn_index": 1, "idempotency_key": "other-run",
             "files": [str(file.pk)]}, format="json", **headers)
        self.assertEqual(denied.status_code, 409, denied.content)
        self.assertEqual(AgentRunFollowUp.objects.count(), 1)
        file.refresh_from_db()
        self.assertEqual(str(file.run_id), self.run_id)
        self.assertIsNone(file.turn_index)
