"""Actual scoped callback HTTP under the commercial-import deny guard.

Uses persisted Run fixtures, not a hosted Agent or live Browser. The separate
runtime_bridge_probe covers real SDK file/command execution through these URLs.
"""
import base64
from io import BytesIO
from datetime import timedelta
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import uuid

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from PIL import Image
from apps.agents.models import AgentModelUsage
from apps.common.invocation_lifecycle import begin_runtime_invocation
from . import test_agent_file_http as file_http


class PersonalCallbackHTTPTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        file_http.PersonalAgentFileHTTPTests.setUpTestData.__func__(cls)

    def setUp(self):
        file_http.PersonalAgentFileHTTPTests.setUp(self)
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        config = override_settings(MEDIA_ROOT=folder.name)
        config.enable()
        self.addCleanup(config.disable)
        self.delegate = APIClient()
        self.internal = f"/api/v1/internal/agent-runs/{self.run.pk}/"
        self.headers = {"HTTP_X_NEXUS_INTERACTION_TOKEN": self.context.interaction_token}
        self.display_headers = {"HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN": self.display_token()}

    display_token = file_http.PersonalAgentFileHTTPTests.display_token

    def callback(self, method, suffix, data=None, expected=200, headers=None):
        response = getattr(self.delegate, method)(self.internal + suffix, data or {},
            format="json", **(self.headers if headers is None else headers))
        body = getattr(response, "data", None)
        self.assertEqual(response.status_code, expected, body)
        return body

    def test_events_plan_chat_and_context_http_survive_reload(self):
        path = f"/api/v1/internal/display/runs/{self.run.pk}/events/"
        headers = {"HTTP_X_NEXUS_AGUI_TOKEN": self.context.write_token}
        self.assertEqual(self.delegate.post(path, {"type": "CUSTOM"}, format="json").status_code, 403)
        for event in (
            {"type": "TEXT_MESSAGE_START", "messageId": "answer", "role": "assistant"},
            {"type": "TEXT_MESSAGE_CONTENT", "messageId": "answer", "delta": "HTTP 回调回答"},
            {"type": "TEXT_MESSAGE_END", "messageId": "answer"},
            {"type": "CUSTOM", "name": "nexus.plan", "value": {
                "steps": [{"title": "Real callback", "status": "completed"}]}},
        ):
            response = self.delegate.post(path, event, format="json", **headers)
            self.assertEqual(response.status_code, 201, response.data)
        for _ in range(2):
            display = self.client.get(self.base + "display/", **self.display_headers)
            self.assertEqual(display.status_code, 200, display.data)
            self.assertEqual(display.data["messages"][-1]["content"], "HTTP 回调回答")
            self.assertNotIn("billing", display.data)
            events = self.client.get(self.base + "events/", **self.display_headers)
            self.assertTrue(any(item.get("name") == "nexus.plan" for item in events.data))
        self.callback("get", "context/", headers={"HTTP_X_NEXUS_CONTEXT_TOKEN": self.context.context_token})
        self.callback("get", "context/", headers=self.headers, expected=404)

    def test_frame_callback_keeps_protected_image_and_strips_sensitive_browser_data(self):
        image = BytesIO()
        Image.new("RGB", (2, 2), "white").save(image, format="PNG")
        asset = self.callback("post", "display-assets/", {
            "content_type": "image/png",
            "content_base64": base64.b64encode(image.getvalue()).decode(),
        }, expected=201)
        path = f"/api/v1/internal/display/runs/{self.run.pk}/events/"
        headers = {"HTTP_X_NEXUS_AGUI_TOKEN": self.context.write_token}
        response = self.delegate.post(path, {
            "type": "CUSTOM", "name": "nexus.computer.frame", "value": {
                "screenshot_url": asset["url"], "width": 2, "height": 2,
                "url": "https://person:password@example.test/search?token=private&q=wafer#secret",
                "observation_id": "observation-7", "revision": 7,
                "action": "locator_click", "action_status": "succeeded", "dom_node_count": 42,
                "dom": [{"text": "private-dom"}], "html": "private-html",
                "typed_value": "private-input", "internal_mcp_url": "http://127.0.0.1:9999/mcp",
            },
        }, format="json", **headers)
        self.assertEqual(response.status_code, 201, response.data)
        value = response.data["value"]
        self.assertEqual(value["screenshot_url"], asset["url"])
        for field, expected in (("width", 2), ("height", 2), ("revision", 7),
                ("observation_id", "observation-7"), ("action", "locator_click"),
                ("action_status", "succeeded"), ("dom_node_count", 42)):
            self.assertEqual(value[field], expected)
        self.assertIn("q=wafer", value["url"])
        for secret in ("person:password", "private", "#secret"):
            self.assertNotIn(secret, value["url"])
        for field in ("dom", "html", "typed_value", "internal_mcp_url"):
            self.assertNotIn(field, value)
        foreign_url = asset["url"].replace(str(self.run.pk), str(uuid.uuid4()))
        foreign_agent_url = f"/api/v1/public/agents/{uuid.uuid4()}/display/runs/{self.run.pk}/assets/{asset['id']}/"
        before = self.run.events.count()
        for invalid in ({"title": "No image"}, {"screenshot_url": "/api/v1/account/me/"},
                {"screenshot_url": foreign_url}, {"image_url": foreign_url},
                {"screenshot_url": foreign_agent_url},
                {"screenshot_url": "https://personal.example" + foreign_url},
                {"screenshot_url": "https://personal.example/unused/.." + foreign_url},
                {"screenshot_url": "https://personal.example" + foreign_url.replace("/agent-runs/", "/%61gent-runs/")},
                {"content_url": "https://personal.example" + foreign_url}):
            with self.subTest(invalid=invalid):
                response = self.delegate.post(path, {"type": "CUSTOM", "name": "nexus.computer.frame",
                    "value": invalid}, format="json", **headers)
                self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.run.events.count(), before)

    def test_event_id_retries_are_idempotent_but_changed_content_conflicts(self):
        from apps.agents.models import AgentMemoryItem
        path = f"/api/v1/internal/display/runs/{self.run.pk}/events/"
        headers = {"HTTP_X_NEXUS_AGUI_TOKEN": self.context.write_token,
                   "HTTP_X_NEXUS_AGUI_EVENT_ID": "personal-memory-1"}
        event = {"type": "CUSTOM", "name": "nexus.memory.item",
                 "value": {"content_text": "Remember once", "memory_type": "fact"}}
        first = self.delegate.post(path, event, format="json", **headers)
        repeated = self.delegate.post(path, event, format="json", **headers)
        for response in (first, repeated):
            self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(first.data["id"], repeated.data["id"])
        event["value"]["content_text"] = "Different content"
        conflict = self.delegate.post(path, event, format="json", **headers)
        self.assertEqual(conflict.status_code, 409, conflict.data)
        items = AgentMemoryItem.objects.filter(agent=self.agent, source_run=self.run)
        self.assertEqual(items.count(), 1)
        self.assertEqual(items.get().content_text, "Remember once")

    def test_standard_activity_state_and_step_callback_shapes_are_preserved(self):
        path = f"/api/v1/internal/display/runs/{self.run.pk}/events/"
        headers = {"HTTP_X_NEXUS_AGUI_TOKEN": self.context.write_token}
        events = (
            {"type": "ACTIVITY_SNAPSHOT", "messageId": "task-plan", "activityType": "PLAN",
             "content": {"steps": [{"label": "Plan", "state": "running"}]}},
            {"type": "ACTIVITY_DELTA", "messageId": "task-plan", "activityType": "PLAN",
             "patch": [{"op": "replace", "path": "/steps/0/state", "value": "done"}]},
            {"type": "STATE_SNAPSHOT", "snapshot": {"runtime": {"status": "active"}}},
            {"type": "STEP_STARTED", "stepName": "retrieve-sources"},
        )
        for event in events:
            with self.subTest(event=event["type"]):
                response = self.delegate.post(path, event, format="json", **headers)
                self.assertEqual(response.status_code, 201, response.data)
                for key, expected in event.items():
                    self.assertEqual(response.data[key], expected)
                self.assertNotIn("value", response.data)

    def test_inline_question_roundtrip_and_cross_run_rejection(self):
        self.run.interaction_mode = "task"
        self.run.save(update_fields=["interaction_mode"])
        question = self.callback("post", "interactions/", expected=201,
            data={"key": "question", "kind": "select", "prompt": "Which?", "choices": [
                {"value": "a", "label": "A"}, {"value": "b", "label": "B"}]})
        suffix = f"interactions/{question['id']}/"
        self.assertEqual(self.callback("get", suffix)["status"], "pending")
        reply = self.client.post(self.base + suffix + "reply/", {"value": "b"},
            format="json", **self.display_headers)
        self.assertEqual(reply.status_code, 200, reply.data)
        self.assertEqual(self.callback("get", suffix)["status"], "answered")
        self.callback("get", f"interactions/{uuid.uuid4()}/", expected=404)
        self.callback("get", suffix, headers={"HTTP_X_NEXUS_INTERACTION_TOKEN": "forged"}, expected=404)

    def test_checkpoint_revision_size_and_expired_token(self):
        self.assertEqual(self.callback("get", "checkpoint/"), {})
        data = {"stage": "read", "data": {"path": "input.txt"}, "expected_revision": 0}
        self.assertEqual(self.callback("put", "checkpoint/", data)["revision"], 1)
        self.callback("put", "checkpoint/", data, expected=409)
        data["expected_revision"] = 1
        self.assertEqual(self.callback("put", "checkpoint/", data)["revision"], 2)
        self.callback("put", "checkpoint/", {"stage": "big", "data": {"value": "中" * 65536}}, expected=400)
        self.run.interaction_token_expires_at = timezone.now() - timedelta(seconds=1)
        self.run.save(update_fields=["interaction_token_expires_at"])
        self.callback("get", "checkpoint/", expected=404)

    def test_usage_is_idempotent_turn_bound_and_not_financial(self):
        request = SimpleNamespace(user=self.row.owner, tenant_id=str(self.row.tenant_id),
            project_id=str(self.row.project_id), headers={}, META={}, query_params={})
        receipt = begin_runtime_invocation(request=request, tenant=self.row.tenant, agent=self.agent,
            runtime=self.runtime, api_key=None, tool_name="echo", display_run=self.run, turn_index=1)
        data = {"event_id": "usage-1", "model": "local", "context_window": 4096,
            "input_tokens": 10, "output_tokens": 20, "turn_index": 1}
        for _ in range(2):
            self.assertTrue(self.callback("put", "usage/", data)["accepted"])
        self.assertEqual(AgentModelUsage.objects.filter(invocation_id=receipt.pk).count(), 1)
        self.callback("put", "usage/", {**data, "output_tokens": 21}, expected=409)
        self.callback("put", "usage/", {**data, "turn_index": 2}, expected=404)
        self.assertFalse(hasattr(receipt, "cost"))
        self.callback("put", "billing-report/", {"amount": "1"}, expected=404)

    def test_display_asset_upload_validation_and_protected_retrieval(self):
        image = BytesIO()
        Image.new("RGB", (1, 1), "white").save(image, format="PNG")
        encoded = base64.b64encode(image.getvalue()).decode()
        data = {"content_type": "image/png", "content_base64": encoded}
        self.callback("post", "display-assets/", {**data, "sha256": "0" * 64}, expected=400)
        asset = self.callback("post", "display-assets/", data, expected=201)
        path = self.internal + f"display-assets/{asset['id']}/"
        for url, client, headers in ((path, self.delegate, self.headers),
                (asset["url"], self.client, self.display_headers)):
            response = client.get(url, **headers)
            try:
                self.assertEqual(response.status_code, 200, getattr(response, "data", None))
                self.assertEqual(b"".join(response.streaming_content), base64.b64decode(encoded))
            finally:
                response.close()
        self.assertEqual(self.delegate.get(path).status_code, 404)
        forged = path.replace(str(self.run.pk), str(uuid.uuid4()))
        self.assertEqual(self.delegate.get(forged, **self.headers).status_code, 404)

    def test_broken_png_checksum_returns_validation_error_not_server_error(self):
        encoded = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a4i0AAAAASUVORK5CYII="
        self.callback("post", "display-assets/", {"content_type": "image/png", "content_base64": encoded}, expected=400)
        self.assertFalse(self.run.display_assets.exists())

    def test_browser_without_attached_computer_has_no_local_fallback(self):
        value = self.callback("post", "browser/", {"operation": "open", "url": "https://example.test"}, expected=409)
        self.assertIn("Attach", str(value))
        from apps.agents.models import AgentBrowserSession
        self.assertFalse(AgentBrowserSession.objects.exists())
