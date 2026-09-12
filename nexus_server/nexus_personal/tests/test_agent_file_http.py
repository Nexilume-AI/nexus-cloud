"""Real caller/delegate HTTP, local storage and Display download authorization.

Run creation uses the shared context service; no fake Computer or Agent runner.
This deliberately does not claim full Private Display or network WSS acceptance.
"""
import hashlib
from datetime import timedelta
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.agents import file_transfers as transfers, runtime_services
from apps.agents.models import Agent, AgentRuntimeImage, AgentRuntimeDeployment, AgentFileTransfer
from apps.datasets.downloads import cookie_name
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


class PersonalAgentFileHTTPTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="http-file-owner@example.test", password=PASSWORD)
        cls.token = Token.objects.create(user=cls.row.owner)
        cls.agent = Agent.objects.create(tenant=cls.row.tenant, project=cls.row.project,
            created_by=cls.row.owner, name="Private files", status="active")
        image = AgentRuntimeImage.objects.create(tenant=cls.row.tenant, project=cls.row.project,
            agent=cls.agent, image_ref="private-file-http:unit", created_by=cls.row.owner)
        cls.runtime = AgentRuntimeDeployment.objects.create(tenant=cls.row.tenant, project=cls.row.project,
            agent=cls.agent, image=image, status="active", health_status="healthy")

    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        config = override_settings(NEXUS_DATASET_STORAGE_ROOT=folder.name)
        config.enable()
        self.addCleanup(config.disable)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.token.key)
        request = SimpleNamespace(user=self.row.owner, tenant_id=str(self.row.tenant_id),
            project_id=str(self.row.project_id), headers={}, META={}, query_params={},
            build_absolute_uri=lambda path: "https://personal.test" + path)
        self.run, self.context = runtime_services.create_invocation_display_context(
            runtime=self.runtime, tool_name="echo", request=request)
        self.base = f"/api/v1/agent-runs/{self.run.pk}/"
        self.internal = f"/api/v1/internal/agent-runs/{self.run.pk}/files/"

    def display_token(self):
        response = self.client.post(self.base + "display-token/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data["display_token"]

    def bytes(self, response, status=200):
        self.assertEqual(response.status_code, status, getattr(response, "data", None))
        try:
            return b"".join(response.streaming_content)
        finally:
            response.close()

    def upload(self, body=b"private-file", *, internal=False):
        client = APIClient() if internal else self.client
        headers = {"HTTP_X_NEXUS_INTERACTION_TOKEN": self.context.interaction_token} if internal else {}
        base = self.internal if internal else "/api/v1/agent-files/"
        data = {"name": "输入.txt", "size_bytes": len(body), "content_type": "text/plain",
                "sha256": hashlib.sha256(body).hexdigest()}
        if not internal:
            data["agent_id"] = str(self.agent.pk)
        response = client.post(base, data, format="json", **headers)
        self.assertEqual(response.status_code, 201, response.data)
        pk = response.data["file_id"]
        path = base + pk + "/"
        response = client.put(path, body, content_type="application/octet-stream",
            HTTP_X_NEXUS_UPLOAD_OFFSET="0", HTTP_X_NEXUS_CHUNK_SHA256=hashlib.sha256(body).hexdigest(), **headers)
        self.assertEqual(response.status_code, 200, response.data)
        response = client.post(path, {}, format="json", **headers)
        self.assertEqual(response.status_code, 202, response.data)
        self.assertTrue(transfers.process_one())
        row = AgentFileTransfer.objects.get(pk=pk)
        self.assertEqual(row.state, "ready", row.error_code)
        return row

    def test_http_upload_unicode_download_head_and_ranges(self):
        body = "文件正文\nsecond line".encode()
        row = self.upload(body)
        path = f"/api/v1/agent-files/{row.pk}/download/"
        response = self.client.get(path)
        self.addCleanup(response.close)
        self.assertIn("no-store", response["Cache-Control"].split(", "))
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.bytes(response), body)
        response = self.client.head(path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(int(response["Content-Length"]), len(body))
        self.assertEqual(self.bytes(self.client.get(path, HTTP_RANGE="bytes=1-4"), 206), body[1:5])
        self.assertEqual(self.bytes(self.client.get(path, HTTP_RANGE="bytes=-4"), 206), body[-4:])
        self.assertEqual(self.client.get(path, HTTP_RANGE="bytes=999-1000").status_code, 416)

    def test_bound_input_requires_run_token_and_old_tabs_remain_valid(self):
        row = self.upload()
        transfers.bind_files(run=self.run, rows=[row])
        old, new = self.display_token(), self.display_token()
        path = self.base + f"files/{row.pk}/download/"
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get(path, HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN="wrong").status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/agent-files/{row.pk}/download/").status_code, 404)
        for token in (old, new):
            self.assertEqual(self.bytes(self.client.get(path, HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN=token)), b"private-file")
        response = self.client.get(self.base + "files/?paged=1&q=输入&limit=1",
            HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN=new)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([item["file_id"] for item in response.data["items"]], [str(row.pk)])

    def test_download_cookie_is_exact_path_bound_and_hidden_run_is_denied(self):
        row = self.upload()
        transfers.bind_files(run=self.run, rows=[row])
        path = self.base + f"files/{row.pk}/download/"
        response = self.client.post(path, {}, format="json", HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN=self.display_token())
        self.assertEqual(response.status_code, 200, response.data)
        name = cookie_name(path)
        cookie = response.cookies[name]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Strict")
        native = APIClient()
        native.cookies[name] = cookie.value
        self.assertEqual(self.bytes(native.get(path)), b"private-file")
        wrong_path = f"/api/v1/agent-files/{row.pk}/download/"
        native.cookies[cookie_name(wrong_path)] = cookie.value
        self.assertEqual(native.get(wrong_path).status_code, 404)
        self.run.caller_hidden_at = timezone.now()
        self.run.save(update_fields=["caller_hidden_at"])
        self.assertEqual(native.get(path).status_code, 404)

    def test_caller_scope_and_file_run_binding_cannot_be_forged(self):
        row = self.upload()
        transfers.bind_files(run=self.run, rows=[row])
        token = self.display_token()
        row.caller_subject_hash = "other-caller"
        row.save(update_fields=["caller_subject_hash"])
        self.assertEqual(self.client.get(self.base + f"files/{row.pk}/download/",
            HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN=token).status_code, 404)
        self.run.caller_subject_hash = "other-caller"
        self.run.save(update_fields=["caller_subject_hash"])
        self.assertEqual(self.client.post(self.base + "display-token/", {}, format="json").status_code, 404)
        self.assertEqual(self.client.get(self.base + "files/", HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN=token).status_code, 404)

    def test_other_authenticated_user_and_cross_run_download_are_rejected(self):
        row = self.upload()
        transfers.bind_files(run=self.run, rows=[row])
        token = self.display_token()
        other = get_user_model().objects.create_user(username="other-file-user", email="other-file@example.test")
        other_client = APIClient()
        other_client.credentials(HTTP_AUTHORIZATION="Bearer " + Token.objects.create(user=other).key)
        path = self.base + f"files/{row.pk}/download/"
        self.assertIn(other_client.get(path, HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN=token).status_code, (401, 403, 404))
        self.assertIn(other_client.post(self.base + "display-token/", {}, format="json").status_code, (401, 403, 404))
        request = SimpleNamespace(user=self.row.owner, tenant_id=str(self.row.tenant_id),
            project_id=str(self.row.project_id), headers={}, META={}, query_params={},
            build_absolute_uri=lambda path: "https://personal.test" + path)
        other_run, _ = runtime_services.create_invocation_display_context(
            runtime=self.runtime, tool_name="echo", request=request)
        other_base = f"/api/v1/agent-runs/{other_run.pk}/"
        other_token = self.client.post(other_base + "display-token/", {}, format="json").data["display_token"]
        self.assertEqual(self.client.get(path, HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN=other_token).status_code, 404)
        self.assertEqual(self.client.get(other_base + f"files/{row.pk}/download/",
            HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN=other_token).status_code, 404)

    def test_download_cookie_is_invalid_after_password_change_and_owner_disable(self):
        row = self.upload()
        path = f"/api/v1/agent-files/{row.pk}/download/"
        response = self.client.post(path, {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        name = cookie_name(path)
        native = APIClient()
        native.cookies[name] = response.cookies[name].value
        self.assertEqual(self.bytes(native.get(path)), b"private-file")
        old_hash = self.row.owner.password
        self.row.owner.set_password(PASSWORD + "-rotated")
        self.row.owner.save(update_fields=["password"])
        self.assertEqual(native.get(path).status_code, 404)
        self.row.owner.password = old_hash
        self.row.owner.is_active = False
        self.row.owner.save(update_fields=["password", "is_active"])
        response = native.get(path)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "PERSONAL_SETUP_REQUIRED")

    def test_turn_filter_preserves_first_use_and_reused_message_references(self):
        row = self.upload()
        transfers.bind_files(run=self.run, rows=[row], turn_index=1)
        url = self.base + f"files/{row.pk}/download/"
        self.run.messages.create(sequence=1, role="user", turn_index=2, content="Use this again",
            content_blocks=[{"type": "file", "url": url, "name": row.name}])
        # Text, malformed legacy entries and a different Run URL must not match.
        self.run.messages.create(sequence=2, role="user", turn_index=3, content=url,
            content_blocks=["plain text", 1, None, {"type": "text", "url": url},
                            {"type": "file", "url": "/other-run/"}])
        token = self.display_token()
        for turn, expected in ((1, [str(row.pk)]), (2, [str(row.pk)]), (3, []), ("unknown", [])):
            response = self.client.get(self.base + f"files/?paged=1&turn={turn}",
                HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN=token)
            self.assertEqual(response.status_code, 200, getattr(response, "data", None))
            self.assertEqual([item["file_id"] for item in response.data["items"]], expected)
        row.refresh_from_db()
        self.assertEqual(row.turn_index, 1)

    def test_invalid_upload_headers_and_integrity_failures_do_not_publish_bytes(self):
        response = self.client.post("/api/v1/agent-files/", {
            "agent_id": str(self.agent.pk), "name": "bad.txt", "size_bytes": 3,
            "sha256": "0" * 64}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        path = "/api/v1/agent-files/" + response.data["file_id"] + "/"
        self.assertEqual(self.client.put(path, b"abc", content_type="application/octet-stream").status_code, 400)
        response = self.client.put(path, b"abc", content_type="application/octet-stream",
            HTTP_X_NEXUS_UPLOAD_OFFSET="0", HTTP_X_NEXUS_CHUNK_SHA256=hashlib.sha256(b"abc").hexdigest())
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.client.post(path, {}, format="json").status_code, 202)
        self.assertTrue(transfers.process_one())
        self.assertEqual(self.client.get(path).data["state"], "failed")
        self.assertEqual(self.client.get(path + "download/").status_code, 404)

    def test_delegate_output_upload_and_download_use_real_scoped_token(self):
        row = self.upload("Agent 产物".encode(), internal=True)
        self.assertEqual(row.run_id, self.run.pk)
        self.assertEqual(row.direction, "output")
        self.assertIsNotNone(row.artifact_id)
        client = APIClient()
        path = self.internal + f"{row.pk}/?download=1"
        self.assertEqual(self.bytes(client.get(path, HTTP_X_NEXUS_INTERACTION_TOKEN=self.context.interaction_token)), "Agent 产物".encode())
        self.assertEqual(client.get(path, HTTP_X_NEXUS_INTERACTION_TOKEN="wrong").status_code, 404)
        self.run.interaction_token_expires_at = timezone.now() - timedelta(seconds=1)
        self.run.save(update_fields=["interaction_token_expires_at"])
        self.assertEqual(client.get(path, HTTP_X_NEXUS_INTERACTION_TOKEN=self.context.interaction_token).status_code, 404)

    def test_delegate_cannot_mutate_input_or_access_finished_run(self):
        row = self.upload()
        transfers.bind_files(run=self.run, rows=[row])
        client = APIClient()
        headers = {"HTTP_X_NEXUS_INTERACTION_TOKEN": self.context.interaction_token}
        path = self.internal + f"{row.pk}/"
        self.assertEqual(client.get(path, **headers).status_code, 200)
        self.assertEqual(client.post(path, {}, format="json", **headers).status_code, 404)
        self.assertEqual(client.delete(path, **headers).status_code, 404)
        self.run.status = "completed"
        self.run.save(update_fields=["status"])
        self.assertEqual(client.get(path, **headers).status_code, 404)

    def test_unauthenticated_upload_and_missing_computer_grant_are_rejected(self):
        self.assertEqual(APIClient().get("/api/v1/agent-files/").status_code, 401)
        self.assertEqual(self.client.get(f"/api/v1/agents/{self.agent.pk}/computer-files/").status_code, 403)
        self.assertEqual(self.client.post(f"/api/v1/agents/{self.agent.pk}/input-files/import/",
            {"path": "secret.txt"}, format="json").status_code, 403)
