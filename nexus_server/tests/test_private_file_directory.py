from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import exceptions
from apps.common.subjects import request_subject
from apps.tenancy.models import Tenant, Membership
from apps.agents.models import Agent, AgentDisplayRun, AgentFileTransfer, AgentOutputArtifact, AgentDisplayEvent, AgentRunMessage
from apps.agents.file_transfers import prepare_files, bind_files


class PrivateFileDirectoryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="file-owner")
        self.tenant = Tenant.objects.create(name="Files", slug="private-files")
        Membership.objects.create(tenant=self.tenant, user=self.user, role="owner")
        self.agent = Agent.objects.create(tenant=self.tenant, created_by=self.user, name="Files")
        self.request = SimpleNamespace(user=self.user, tenant_id=self.tenant.pk, headers={})
        self.run = AgentDisplayRun.objects.create(agent=self.agent, tenant=self.tenant, consumer_tenant=self.tenant,
            run_kind="invocation", caller_subject_hash=request_subject(self.request).subject_hash, write_token="unused")
        self.client = APIClient(); self.client.force_authenticate(self.user)
        self.headers = {"HTTP_X_NEXUS_TENANT": str(self.tenant.pk)}
        self.base = f"/api/v1/agent-runs/{self.run.pk}"
        response = self.client.post(self.base + "/display-token/", {}, format="json", **self.headers)
        self.assertEqual(response.status_code, 200, response.content)
        self.headers["HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN"] = response.json()["data"]["display_token"]

    def files(self, count):
        return AgentFileTransfer.objects.bulk_create([AgentFileTransfer(tenant=self.tenant, agent=self.agent, run=self.run,
            caller_subject_hash=self.run.caller_subject_hash, direction="input", name=f"file-{i}.txt", content_type="text/plain",
            size_bytes=1, state="ready", storage_backend="local", object_key=f"test/{i}", turn_index=1,
            expires_at=timezone.now() + timedelta(days=1)) for i in range(count)])

    def output(self):
        return AgentOutputArtifact.objects.create(tenant=self.tenant, agent=self.agent, run=self.run,
            workspace_path="report.txt", original_file_name="report.txt", content_type="text/plain", size_bytes=1,
            snapshot_status="ready", scan_status="passed", policy_status="approved", snapshot_storage_backend="local",
            snapshot_object_key="run-owned/report", sha256="a" * 64, turn_index=1)

    def test_private_output_reads_never_replay_history_or_write(self):
        artifact = self.output()
        AgentDisplayEvent.objects.create(run=self.run, agent=self.agent, tenant=self.tenant, seq=1, event_type="CUSTOM",
            payload_json={"name": "nexus.file.created", "value": {"path": "old.txt"}})
        with patch("apps.agents.views.sync_output_artifacts_from_run") as replay, CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.base + "/outputs/?paged=1", **self.headers)
        self.assertEqual(response.status_code, 200, response.content)
        replay.assert_not_called()
        self.assertEqual(response.json()["data"]["items"][0]["turn_index"], 1)
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))
        self.assertEqual(list(self.run.output_artifacts.values_list("pk", flat=True)), [artifact.pk])

    def test_pages_reach_new_and_old_inputs_over_200(self):
        rows = self.files(213); seen = []; cursor = ""
        while True:
            response = self.client.get(self.base + "/files/", {"paged": 1, "cursor": cursor}, **self.headers)
            self.assertEqual(response.status_code, 200, response.content)
            payload = response.json()["data"]
            self.assertLessEqual(len(payload["items"]), 50)
            seen += [item["file_id"] for item in payload["items"]]
            cursor = payload["next_cursor"]
            if not cursor: break
        self.assertEqual(set(seen), {str(row.pk) for row in rows})
        self.assertEqual(len(seen), 213)

    def test_completion_does_not_replay_events_or_snapshot_previous_turn(self):
        from apps.agents.runtime_services import snapshot_invocation_outputs
        self.output()
        # The previous-turn artifact is pending, but must never be read from the
        # current Computer as if it were a new-turn output.
        self.run.output_artifacts.update(snapshot_status="pending", snapshot_object_key="")
        self.run.computer_binding_id = 123  # only the early guard; no connection is accessed
        with patch("apps.agents.services._run_message_turn", return_value=2), \
                patch("apps.agents.services.sync_output_artifacts_from_run") as replay, \
                patch("apps.workspaces.services.read_workspace_file") as read:
            snapshot_invocation_outputs(run=self.run)
        replay.assert_not_called()
        read.assert_not_called()
        self.assertEqual(self.run.output_artifacts.count(), 1)

    def test_server_search_reuse_turn_and_cursor_isolation(self):
        rows = self.files(3)
        AgentRunMessage.objects.create(run=self.run, sequence=1, turn_index=9, role="user", content="reuse", content_blocks=[
            {"type": "file", "url": f"{self.base}/files/{rows[0].pk}/download/"}])
        result = self.client.get(self.base + "/files/?paged=1&turn=9", **self.headers)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual([item["file_id"] for item in result.json()["data"]["items"]], [str(rows[0].pk)])
        first = self.client.get(self.base + "/files/?paged=1&limit=1", **self.headers).json()["data"]
        for endpoint in ("outputs/", "files/?q=other&"):
            url = self.base + "/" + endpoint + ("?" if "?" not in endpoint else "")
            response = self.client.get(url + "paged=1&cursor=" + first["next_cursor"], **self.headers)
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(self.base + "/files/?paged=1&limit=500", **self.headers).status_code, 400)

    def test_reference_is_idempotent_and_revocation_is_rechecked(self):
        artifact = self.output()
        def select():
            return self.client.post(self.base + "/file-reference/", {"kind": "output", "id": str(artifact.pk)}, format="json", **self.headers)
        first, second = select(), select()
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json()["data"]["id"], second.json()["data"]["id"])
        row = AgentFileTransfer.objects.get(pk=first.json()["data"]["id"])
        self.assertEqual(row.object_key, artifact.snapshot_object_key)
        self.assertEqual(row.parts.count(), 0)
        blocks = bind_files(run=self.run, rows=[row], turn_index=2)
        self.assertEqual(blocks[0]["url"], f"{self.base}/files/{row.pk}/download/")
        artifact.policy_status = "blocked"; artifact.save()
        with self.assertRaises(exceptions.NotFound): bind_files(run=self.run, rows=[row], turn_index=3)
        self.assertEqual(self.client.get(self.base + f"/file-reference/?kind=input&id={row.pk}", **self.headers).status_code, 404)
        self.assertEqual(self.client.get(self.base + f"/files/{row.pk}/download/", **self.headers).status_code, 404)

    def test_cross_caller_token_and_run_are_not_authority(self):
        row = self.files(1)[0]
        other = get_user_model().objects.create_user(username="other-file-caller")
        Membership.objects.create(tenant=self.tenant, user=other, role="owner")
        self.client.force_authenticate(other)
        for suffix in ("/files/?paged=1", f"/file-reference/?kind=input&id={row.pk}", "/outputs/?paged=1"):
            self.assertEqual(self.client.get(self.base + suffix, **self.headers).status_code, 404)

    def test_existing_input_reference_preserves_first_turn(self):
        row = self.files(1)[0]
        response = self.client.post(self.base + "/file-reference/", {"kind": "input", "id": str(row.pk)}, format="json", **self.headers)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["id"], str(row.pk))
        bind_files(run=self.run, rows=[row], turn_index=7)
        row.refresh_from_db()
        self.assertEqual(row.turn_index, 1)
        self.assertEqual(self.run.file_transfers.count(), 1)

    def test_foreign_run_file_and_blocked_output_cannot_be_selected(self):
        row = self.files(1)[0]
        AgentFileTransfer.objects.filter(pk=row.pk).update(run=None)
        self.assertEqual(self.client.get(self.base + f"/file-reference/?kind=input&id={row.pk}", **self.headers).status_code, 404)
        artifact = self.output()
        artifact.scan_status = "failed"; artifact.save()
        self.assertEqual(self.client.post(self.base + "/file-reference/", {"kind": "output", "id": str(artifact.pk)}, format="json", **self.headers).status_code, 404)
        self.assertFalse(self.run.file_transfers.filter(source_kind="run_output").exists())

    def test_run_image_reuse_validates_bytes_and_never_fetches_external_media(self):
        import tempfile
        import hashlib
        from django.test import override_settings
        from django.core.files.base import ContentFile
        from apps.agents.models import AgentDisplayAsset
        from apps.agents.image_services import prepare_attachments, bind_attachments
        from tests.image_media_guards import png
        data = png()
        with tempfile.TemporaryDirectory() as root, override_settings(MEDIA_ROOT=root):
            asset = AgentDisplayAsset(run=self.run, content_type="image/png", size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            asset.file.save("input.png", ContentFile(data), save=True)
            descriptor = {"input_schema": {"properties": {"attachments": {"type": "array"}}}}
            with patch("apps.agents.image_services.get_media_asset") as external:
                prepared = prepare_attachments(request=self.request, descriptor=descriptor, attachments=[{"asset_id": str(asset.pk)}], run=self.run)
                external.assert_not_called()
            blocks = bind_attachments(run=self.run, prepared=prepared)
            self.assertEqual(blocks[0]["url"], f"{self.base}/display-assets/{asset.pk}/")
            self.assertEqual(self.run.display_assets.count(), 1)
            asset.sha256 = "0" * 64; asset.save()
            with self.assertRaises(exceptions.NotFound):
                prepare_attachments(request=self.request, descriptor=descriptor, attachments=[{"asset_id": str(asset.pk)}], run=self.run)
