"""Shared attachment snapshots and queue/steer delivery, independent of edition authority."""
import json
import tempfile
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.agents import follow_ups, task_execution as worker
from apps.agents.models import AgentDisplayRun, AgentRunFollowUp
from tests.image_media_guards import png
from tests.agent_task_arguments import queued_arguments
from tests.agent_file_guards import FileArgumentGuards


class FollowUpAttachmentHelpers:
    def configure_agent(self):
        self.agent.repo_metadata = {"tools": [{"name": "inspect", "input_schema": {"type": "object", "properties": {
            "message": {"type": "string"}, "attachments": {"type": "array"},
            "files": FileArgumentGuards.file_schema()["properties"]["files"]}, "required": ["message"]}}]}
        self.agent.save()

    def image(self):
        response = self.client.post("/api/v1/media/assets/", {"file": SimpleUploadedFile("evidence.png", png(), content_type="image/png"), "purpose": "chat_input"}, **self._headers(self.project_a))
        self.assertEqual(response.status_code, 201, response.content)
        return self.attachment_payload(response)["id"]

    def send(self, image=None, file=None, **extra):
        data = {"mode": "queue", "content": "Read attachments", "turn_index": 1, "idempotency_key": "attached",
                "attachments": [{"asset_id": image}] if image else [], "files": [str(file.pk)] if file else [], **extra}
        return self.client.post(self.path, data, format="json", **self.headers)


class FollowUpAttachmentGuards:
    def test_queue_snapshots_survive_media_expiry_and_deliver_once_in_next_turn(self):
        image, file = self.image(), self.upload(100)
        response = self.send(image, file)
        self.assertEqual(response.status_code, 202, response.content)
        row = AgentRunFollowUp.objects.get()
        asset = row.run.display_assets.get()
        self.assertNotEqual(str(asset.id), image)
        file.refresh_from_db(); self.assertEqual(str(file.run_id), self.run_id); self.assertIsNone(file.turn_index)
        # Original temporary media may expire; accepted queue owns its snapshot.
        from apps.datasets.models import MediaAsset
        MediaAsset.objects.filter(pk=image).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.send(image, file).status_code, 202)
        self.assertEqual(row.run.display_assets.count(), 1)
        AgentDisplayRun.objects.filter(pk=self.run_id).update(follow_up_attachment_protocol=1)
        self.finish()
        self.assertEqual(follow_ups.dispatch_pending(), 1)
        self.assertEqual(follow_ups.dispatch_pending(), 0)
        self.task.refresh_from_db(); file.refresh_from_db()
        self.assertEqual(file.turn_index, 2)
        self.assertEqual(AgentDisplayRun.objects.get(pk=self.run_id).follow_up_attachment_protocol, 0)
        args = json.loads(queued_arguments(self.task)["body"])["params"]["arguments"]
        self.assertEqual(args["attachments"][0]["asset_id"], str(asset.id))
        self.assertEqual(args["files"][0]["file_id"], str(file.pk))
        self.assertNotIn("source_kind", args["files"][0])
        self.assertIn("source_kind", row.attachment_manifest["files"][0])
        message = row.run.messages.get(source_message_id=f"follow-up-{row.pk}")
        self.assertEqual(message.turn_index, 2)
        self.assertEqual([block["type"] for block in message.content_blocks], ["markdown", "image", "file"])
        self.assertEqual(row.run.display_assets.count(), 1)
        context = queued_arguments(self.task)["display_pair"][1]
        path = f"/api/v1/internal/agent-runs/{self.run_id}/display-assets/{asset.id}/"
        self.assertEqual(self.client.get(path, HTTP_X_NEXUS_INTERACTION_TOKEN=self.token).status_code, 404)
        response = self.client.get(path, HTTP_X_NEXUS_INTERACTION_TOKEN=context.interaction_token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content) if response.streaming else response.content, png())

    def test_steer_requires_negotiation_and_records_actual_consumption(self):
        image, file = self.image(), self.upload(100)
        worker.claim()
        self.assertEqual(self.inbox("configure", mode="steer_and_queue").status_code, 200)
        self.assertEqual(self.send(image, file, mode="steer").status_code, 409)
        self.assertFalse(AgentRunFollowUp.objects.exists())
        self.assertEqual(self.inbox("configure", mode="steer_and_queue", attachment_protocol=1).status_code, 200)
        self.assertEqual(self.send(image, file, mode="steer").status_code, 202)
        self.assertEqual(self.inbox("configure", mode="steer_and_queue").status_code, 409)
        received = self.inbox("receive")
        self.assertEqual(received.status_code, 200, received.content)
        item = self.attachment_payload(received)["items"][0]
        self.assertEqual(len(item["attachments"]), 1); self.assertEqual(len(item["files"]), 1)
        file.refresh_from_db(); self.assertEqual(file.turn_index, 1)
        self.assertEqual(self.inbox("receive").status_code, 200)
        self.assertEqual(self.task.run.messages.filter(content="Read attachments").count(), 1)
        self.assertEqual(self.inbox("acknowledge", message_id=item["id"]).status_code, 200)

    def test_manual_next_turn_delivers_legacy_file_contract(self):
        file = self.upload(5)
        self.finish()
        response = self.client.post(f"/api/v1/agent-runs/{self.run_id}/resume/",
            {"content": "Read this file", "files": [str(file.pk)]}, format="json", **self.headers)
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(self.attachment_payload(response)["run_id"], self.run_id)
        self.task.refresh_from_db(); file.refresh_from_db()
        self.assertEqual(file.turn_index, 2)
        queued = queued_arguments(self.task)
        args = json.loads(queued["body"])["params"]["arguments"]
        self.assertEqual(set(args["files"][0]), {"file_id", "name", "content_type", "size_bytes", "sha256"})
        self.assertEqual(queued["display_pair"][1].input_files[0]["source_kind"], "upload")

    def test_cancel_retains_audit_but_never_dispatches_or_consumes_files(self):
        file = self.upload(100)
        row = self.attachment_payload(self.send(file=file))
        self.assertEqual(self.client.delete(self.path + row["id"] + "/", **self.headers).status_code, 200)
        self.finish(); self.assertEqual(follow_ups.dispatch_pending(), 0)
        file.refresh_from_db(); self.assertIsNone(file.turn_index)
        self.assertFalse(self.task.run.messages.filter(content="Read attachments").exists())

    def test_missing_snapshot_blocks_dispatch_without_silently_dropping_image(self):
        self.assertEqual(self.send(self.image()).status_code, 202)
        self.task.run.display_assets.all().delete()
        self.finish(); self.assertEqual(follow_ups.dispatch_pending(), 0)
        self.assertEqual(AgentRunFollowUp.objects.get().status, "blocked")
        self.assertFalse(self.task.run.messages.filter(content="Read attachments").exists())

    def test_invalid_refs_rejected_and_unavailable_steer_file_is_not_delivered(self):
        for refs in ({"attachments": [{"asset_id": "https://example.com/image"}]}, {"files": ["../../file"]}, {"attachments": [{}]}, {"files": [None]}):
            self.assertEqual(self.send(**refs).status_code, 400)
        file = self.upload(100)
        worker.claim(); self.inbox("configure", mode="steer_and_queue", attachment_protocol=1)
        self.assertEqual(self.send(file=file, mode="steer").status_code, 202)
        file.state = "canceled"; file.save(update_fields=["state"])
        received = self.inbox("receive")
        self.assertEqual(received.status_code, 200)
        self.assertEqual(self.attachment_payload(received)["items"][0]["status"], "rejected")
        self.assertFalse(self.task.run.messages.filter(content="Read attachments").exists())
