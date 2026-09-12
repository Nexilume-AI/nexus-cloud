"""Actual Private Display HTTP/state with all commercial imports denied.

The file HTTP fixture supplies real owner auth, Run Context and local storage.
Browser asset tests verify protected retrieval, not actual browser execution.
"""
import base64
import hashlib
from tempfile import TemporaryDirectory
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from apps.agents import services, runtime_services
from apps.agents.models import AgentDisplayAsset
from apps.common.invocation_lifecycle import begin_runtime_invocation
from . import test_agent_file_http as file_http


class PersonalPrivateDisplayHTTPTests(TestCase):
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
        self.headers = {"HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN": self.display_token()}

    display_token = file_http.PersonalAgentFileHTTPTests.display_token
    upload = file_http.PersonalAgentFileHTTPTests.upload
    bytes = file_http.PersonalAgentFileHTTPTests.bytes

    def display(self):
        response = self.client.get(self.base + "display/", **self.headers)
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        return response.data

    def test_real_invocation_payload_retains_messages_plan_and_excludes_financial_schema(self):
        from types import SimpleNamespace
        request = SimpleNamespace(user=self.row.owner, tenant_id=str(self.row.tenant_id),
            project_id=str(self.row.project_id), headers={}, META={}, query_params={})
        invocation = begin_runtime_invocation(request=request, tenant=self.row.tenant, agent=self.agent,
            runtime=self.runtime, api_key=None, tool_name="echo", display_run=self.run, turn_index=1)
        self.assertFalse(hasattr(invocation, "cost"))
        for kind, payload in (
            ("TEXT_MESSAGE_START", {"messageId": "answer", "role": "assistant"}),
            ("TEXT_MESSAGE_CONTENT", {"messageId": "answer", "delta": "真实回答"}),
            ("TEXT_MESSAGE_END", {"messageId": "answer"}),
            ("CUSTOM", {"name": "nexus.plan", "value": {"steps": [{"title": "Read", "status": "completed"}]}}),
        ):
            services.append_display_event(run=self.run, event_type=kind, payload=payload)
        value = self.display()
        self.assertNotIn("billing", value)
        self.assertEqual(value["id"], str(self.run.pk))
        self.assertEqual(value["status"], "running")
        self.assertEqual(value["messages"][-1]["content"], "真实回答")
        self.assertIn("usage", value)
        self.assertIn("execution", value)
        serialized = str(value)
        for secret in (self.context.interaction_token, self.context.workspace_delegate_token, self.context.context_token):
            if secret:
                self.assertTrue(secret not in serialized, "Run response must not contain delegate credentials")
        events = self.client.get(self.base + "events/", **self.headers)
        self.assertEqual(events.status_code, 200, events.data)
        self.assertTrue(any(item.get("name") == "nexus.plan" for item in events.data))
        self.assertEqual(self.display()["messages"], value["messages"])
        from apps.agents.observability import optimized_runs
        from apps.agents.models import AgentDisplayRun
        for summary in (True, False):
            rows = list(optimized_runs(AgentDisplayRun.objects.filter(pk=self.run.pk), summary=summary))
            self.assertEqual(rows[0]._obs_tool_name, "echo")
            for field in ("cost", "currency", "authorized_cost", "billing_report", "billing_status"):
                self.assertFalse(hasattr(rows[0], f"_obs_{field}"))
        response = self.client.get(f"/api/v1/agents/{self.agent.pk}/observability/runs/?tool=echo&limit=1")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([item["id"] for item in response.data["items"]], [str(self.run.pk)])
        self.assertNotIn("billing", response.data["items"][0])
        self.assertEqual(response.data["items"][0].get("turn_index"), 1)

    def test_observability_turn_is_operational_and_uses_latest_invocation_without_extra_queries(self):
        from apps.agents.models import AgentDisplayRun, AgentRuntimeInvocation
        from apps.agents.observability import optimized_runs
        from apps.agents.serializers import AgentDisplayRunSerializer
        self.assertIsNone(AgentDisplayRunSerializer(self.run).data.get("turn_index"))
        for turn in (3, 1, 2):
            AgentRuntimeInvocation.objects.create(tenant=self.row.tenant, agent=self.agent,
                display_run=self.run, tool_name="echo", turn_index=turn)
        self.assertEqual(AgentDisplayRunSerializer(self.run).data.get("turn_index"), 3)
        rows = list(optimized_runs(AgentDisplayRun.objects.filter(pk=self.run.pk), summary=True))
        with self.assertNumQueries(0):
            value = AgentDisplayRunSerializer(rows[0]).data
        self.assertEqual(value["turn_index"], 3)
        self.assertNotIn("billing", value)
        response = self.client.get(f"/api/v1/agents/{self.agent.pk}/observability/runs/?limit=1")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["items"][0]["turn_index"], 3)

    def test_multiple_pending_questions_answer_in_place_and_duplicate_is_rejected(self):
        self.run.interaction_mode = "task"
        self.run.save(update_fields=["interaction_mode"])
        rows = [runtime_services.create_run_interaction(run_id=str(self.run.pk), token=self.context.interaction_token,
            data={"key": key, "kind": kind, "prompt": prompt, "choices": choices}) for key, kind, prompt, choices in (
                ("confirm", "confirm", "Continue?", [{"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}]),
                ("select", "select", "Choose", [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]))]
        self.assertEqual([item["status"] for item in self.display()["interactions"]], ["pending", "pending"])
        for interaction, answer in zip(rows, ("yes", "b")):
            path = self.base + f"interactions/{interaction['id']}/reply/"
            self.assertEqual(self.client.post(path, {"value": "forged"}, format="json", **self.headers).status_code, 400)
            response = self.client.post(path, {"value": answer}, format="json", **self.headers)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(self.client.post(path, {"value": answer}, format="json", **self.headers).status_code, 404)
        value = self.display()
        self.assertEqual([item["status"] for item in value["interactions"]], ["answered", "answered"])
        self.assertEqual([item["content"] for item in value["messages"] if item["role"] == "user"], ["yes", "b"])

    def test_display_events_and_terminal_are_caller_and_token_bound(self):
        for suffix in ("display/", "events/", "outputs/", "terminal/"):
            self.assertEqual(APIClient().get(self.base + suffix).status_code, 401)
            self.assertEqual(self.client.get(self.base + suffix).status_code, 404)
            self.assertEqual(self.client.get(self.base + suffix, HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN="forged").status_code, 404)
        response = self.client.get(self.base + "terminal/", **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "not_started")
        self.run.caller_subject_hash = "foreign"
        self.run.save(update_fields=["caller_subject_hash"])
        for suffix in ("display/", "events/", "outputs/", "terminal/"):
            self.assertEqual(self.client.get(self.base + suffix, **self.headers).status_code, 404)

    def test_protected_browser_frame_retrieval_and_hidden_run_denial(self):
        body = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a4i0AAAAASUVORK5CYII=")
        asset = AgentDisplayAsset.objects.create(run=self.run, file=ContentFile(body, name="frame.png"),
            content_type="image/png", size_bytes=len(body), sha256=hashlib.sha256(body).hexdigest(), width=1, height=1)
        path = self.base + f"display-assets/{asset.pk}/"
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.bytes(self.client.get(path, **self.headers)), body)
        self.run.caller_hidden_at = timezone.now()
        self.run.save(update_fields=["caller_hidden_at"])
        self.assertEqual(self.client.get(path, **self.headers).status_code, 404)

    def test_real_output_snapshot_directory_download_and_rename_after_refresh(self):
        row = self.upload(b"run output", internal=True)
        response = self.client.get(self.base + "outputs/?paged=1&turn=1", **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([item["id"] for item in response.data["items"]], [str(row.artifact_id)])
        path = self.base + f"outputs/{row.artifact_id}/download/"
        self.assertEqual(self.bytes(self.client.get(path, **self.headers)), b"run output")
        response = self.client.patch(self.base + "display/", {"title": "Renamed run"}, format="json", **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.display()["display_title"], "Renamed run")
        self.assertEqual(self.client.delete(self.base + "display/", **self.headers).status_code, 409)
        runtime_services.finish_invocation_display_run(run=self.run, succeeded=True)
        self.assertEqual(self.display()["status"], "completed")
        self.assertEqual(self.client.delete(self.base + "display/", **self.headers).status_code, 204)
        self.assertEqual(self.client.get(self.base + "display/", **self.headers).status_code, 404)
        self.assertEqual(self.client.get(path, **self.headers).status_code, 404)

    def test_output_reuse_requires_current_scan_and_policy_approval(self):
        row = self.upload(b"run output", internal=True)
        artifact = row.artifact
        path = self.base + "file-reference/"
        for scan, policy in (("failed", "approved"), ("pending", "approved"), ("passed", "blocked")):
            artifact.scan_status, artifact.policy_status = scan, policy
            artifact.save(update_fields=["scan_status", "policy_status"])
            response = self.client.post(path, {"kind": "output", "id": str(artifact.pk)}, format="json", **self.headers)
            self.assertEqual(response.status_code, 404)
