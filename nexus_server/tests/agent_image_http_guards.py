"""Shared Agent image HTTP/state checks; dispatch remains mocked, not live E2E."""
import json
import base64
from unittest import mock
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from apps.agents.models import AgentDisplayRun, AgentExecutionTask
from apps.agents.services import append_display_event, _safe_run_result_blocks
from tests.image_media_guards import png
from tests.agent_task_arguments import queued_arguments


class AgentImageHTTPGuards:
    def upload(self, data=None, mime="image/png"):
        response = self.client.post("/api/v1/media/assets/", {"file": SimpleUploadedFile("input.png", png() if data is None else data, content_type=mime), "purpose": "chat_input"}, **self._headers(self.project_a))
        self.assertEqual(response.status_code, 201, response.content)
        return self.image_payload(response)["id"]

    def start(self, asset_id):
        with mock.patch("apps.agents.runtime_services.threading.Thread") as thread:
            response = self.client.post(f"/api/v1/agents/{self.agent.id}/private-runs/", {"content": "Describe this image", "attachments": [{"asset_id": asset_id}]}, format="json", **self._headers(self.project_a))
        return response, thread

    def test_run_snapshot_delegate_and_caller_isolation(self):
        asset_id = self.upload()
        response, thread = self.start(asset_id)
        self.assertEqual(response.status_code, 202, response.content)
        run = AgentDisplayRun.objects.get(id=self.image_payload(response)["run_id"])
        asset = run.display_assets.get()
        self.assertNotEqual(str(asset.id), asset_id)
        image_block = run.messages.get(role="user").content_blocks[-1]
        self.assertEqual(image_block["type"], "image")
        self.assertEqual(image_block["title"], "input.png")
        args = json.loads(queued_arguments(run.execution_task)["body"])["params"]["arguments"]
        self.assertEqual(args["attachments"][0]["asset_id"], str(asset.id))
        context = queued_arguments(run.execution_task)["display_pair"][1]
        path = f"/api/v1/internal/agent-runs/{run.id}/display-assets/{asset.id}/"
        read = APIClient().get(path, HTTP_X_NEXUS_INTERACTION_TOKEN=context.interaction_token)
        self.assertEqual(read.status_code, 200)
        self.assertEqual(b"".join(read.streaming_content), png())
        self.assertEqual(APIClient().get(path, HTTP_X_NEXUS_INTERACTION_TOKEN="wrong").status_code, 404)
        display_headers = self._display_headers(str(run.id))
        self.assert_other_caller_cannot_read(run, asset, display_headers)

    def test_agent_output_upload_validates_bytes_and_publishes_protected_chat_image(self):
        response, thread = self.start(self.upload())
        self.assertEqual(response.status_code, 202, response.content)
        run = AgentDisplayRun.objects.get(id=self.image_payload(response)["run_id"])
        context = queued_arguments(run.execution_task)["display_pair"][1]
        headers = {"HTTP_X_NEXUS_INTERACTION_TOKEN": context.interaction_token}
        path = f"/api/v1/internal/agent-runs/{run.id}/display-assets/"
        client = APIClient()
        self.assertEqual(client.get(path, **headers).status_code, 405)
        for raw in (b"not an image", b"<svg/>"):
            rejected = client.post(path, {"content_type": "image/png", "content_base64": base64.b64encode(raw).decode()}, format="json", **headers)
            self.assertEqual(rejected.status_code, 400, rejected.content)
        uploaded = client.post(path, {"content_type": "image/png", "content_base64": base64.b64encode(png()).decode(), "width": 999}, format="json", **headers)
        self.assertEqual(uploaded.status_code, 201, uploaded.content)
        asset = self.image_payload(uploaded)
        self.assertEqual(asset["width"], 4)
        self.assertEqual(client.post(path + asset["id"] + "/", {}, format="json", **headers).status_code, 405)
        append_display_event(run=run, event_type="CUSTOM", payload={"name": "nexus.image.created",
            "value": {"type": "image", "asset_id": asset["id"], "title": "Result"}}, visibility="public")
        message = run.messages.get(role="assistant")
        self.assertEqual(message.content_blocks[0]["url"], asset["url"])
        self.assertNotIn("base64", str(message.content_blocks))

    def test_text_only_tool_and_corrupt_image_rejected_before_dispatch(self):
        asset_id = self.upload(b"not a PNG")
        response, thread = self.start(asset_id)
        self.assertEqual(response.status_code, 400, response.content)
        thread.assert_not_called()
        self.agent.repo_metadata = {}
        self.agent.save()
        response, _ = self.start(self.upload())
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("AGENT_IMAGE_INPUT_UNSUPPORTED", str(response.content))

    def test_output_only_resolves_current_runs_assets(self):
        response, _ = self.start(self.upload())
        self.assertEqual(response.status_code, 202, response.content)
        run = AgentDisplayRun.objects.get(id=self.image_payload(response)["run_id"])
        asset = run.display_assets.get()
        self.assertEqual(_safe_run_result_blocks({"type": "image", "asset_id": str(asset.id)}, run=run)[0]["type"], "image")
        for value in [{"type": "image", "asset_id": "malformed"}, {"type": "image", "url": "https://untrusted.example/secret"}]:
            self.assertEqual(_safe_run_result_blocks(value, run=run), [])

    def test_image_can_be_attached_on_next_turn_in_same_run(self):
        response, _ = self.start(self.upload())
        self.assertEqual(response.status_code, 202, response.content)
        run = AgentDisplayRun.objects.get(id=self.image_payload(response)["run_id"])
        task = run.execution_task
        task.status = AgentExecutionTask.STATUS_COMPLETED
        task.save()
        append_display_event(run=run, event_type="RUN_FINISHED", payload={})
        with mock.patch("apps.agents.runtime_services.threading.Thread"):
            resumed = self.client.post(f"/api/v1/agent-runs/{run.id}/resume/", {"content": "Compare the next image", "attachments": [{"asset_id": self.upload()}]}, format="json", **self._display_headers(str(run.id)))
        self.assertEqual(resumed.status_code, 202, resumed.content)
        self.assertEqual(run.display_assets.count(), 2)
        self.assertEqual(run.messages.filter(role="user").count(), 2)
