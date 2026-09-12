"""Actual owned history/redaction/snapshot HTTP; no fake execution or scan."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.agents import services, runtime_services
from apps.agents.models import AgentDisplayRun, AgentLog
from apps.tenancy.models import Project
from . import test_agent_file_http as files


@override_settings(ROOT_URLCONF="nexus_personal.urls")
class PersonalAgentOperationsHTTPTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        files.PersonalAgentFileHTTPTests.setUpTestData.__func__(cls)

    def setUp(self):
        files.PersonalAgentFileHTTPTests.setUp(self)
        self.agent_base = f"/api/v1/agents/{self.agent.pk}/"
        self.history = self.agent_base + f"display-runs/{self.run.pk}/"

    upload = files.PersonalAgentFileHTTPTests.upload
    bytes = files.PersonalAgentFileHTTPTests.bytes

    def test_logs_and_status_are_real_bounded_and_noncommercial(self):
        AgentLog.objects.bulk_create([AgentLog(agent=self.agent, message=f"line-{i}") for i in range(120)])
        response = self.client.get(self.agent_base + "logs/?tail=9999")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data), 100)
        self.assertEqual(len(self.client.get(self.agent_base + "logs/?tail=2").data), 2)
        status = self.client.get(self.agent_base + "status/")
        self.assertEqual(status.status_code, 200, status.data)
        self.assertEqual(status.data["agent"]["id"], str(self.agent.pk))
        self.assertEqual(status.data["deployments"], [], "Legacy status must not invent a deployment")
        self.assertNotIn("pricing", status.data["agent"])
        self.assertNotIn("publication_status", status.data["agent"])

    def test_history_limit_and_personal_run_schema(self):
        AgentDisplayRun.objects.bulk_create([
            AgentDisplayRun(agent=self.agent, tenant=self.row.tenant, consumer_tenant=self.row.tenant,
                consumer_project=self.row.project, caller_subject_hash=self.run.caller_subject_hash,
                run_kind="invocation", status="completed") for _ in range(105)])
        response = self.client.get(self.agent_base + "display-runs/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data), 100)
        for item in response.data:
            for field in ("billing", "pricing", "write_token", "display_token", "workspace_delegate_token"):
                self.assertNotIn(field, item)

    def test_event_history_filters_private_chat_and_honors_cursor(self):
        services.append_display_event(run=self.run, event_type="TEXT_MESSAGE_CONTENT",
            payload={"messageId": "private-message", "delta": "caller-chat-sentinel"})
        event = services.append_display_event(run=self.run, event_type="CUSTOM",
            payload={"name": "nexus.plan", "value": {"steps": [{"title": "Inspect", "status": "running"}]}})
        response = self.client.get(self.history + "events/?limit=1")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertLessEqual(len(response.data), 1)
        response = self.client.get(self.history + "events/")
        self.assertNotIn("caller-chat-sentinel", str(response.data))
        self.assertIn("nexus.plan", str(response.data))
        response = self.client.get(self.history + f"events/?cursor={event.seq}")
        self.assertEqual(response.data, [])

    def test_redaction_requires_completion_and_preserves_original_evidence(self):
        event = services.append_display_event(run=self.run, event_type="CUSTOM",
            payload={"name": "unit-event", "value": {"password": "test-only-sensitive-marker"}})
        self.assertNotIn("test-only-sensitive-marker", str(self.client.get(self.history + "events/").data))
        response = self.client.post(self.history + "redact/", {}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        runtime_services.finish_invocation_display_run(run=self.run, succeeded=True)
        response = self.client.post(self.history + "redact/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        event.refresh_from_db()
        self.assertIn("test-only-sensitive-marker", str(event.payload_json))
        self.assertNotIn("test-only-sensitive-marker", str(event.redacted_payload_json))
        response = self.client.get(self.history + "events/")
        self.assertNotIn("test-only-sensitive-marker", str(response.data))
        self.run.refresh_from_db()
        self.assertEqual(self.run.redaction_status, "passed")
        token = files.PersonalAgentFileHTTPTests.display_token(self)
        caller_events = self.client.get(self.base + "events/", HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN=token)
        self.assertEqual(caller_events.status_code, 200, caller_events.data)
        self.assertIn("test-only-sensitive-marker", str(caller_events.data))

    def test_generic_mcp_export_is_scoped_template_not_issued_commercial_key(self):
        response = self.client.get(self.agent_base + "mcp/export/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["agent_id"], str(self.agent.pk))
        self.assertEqual(response.data["scope"]["tenant_id"], str(self.row.tenant_id))
        self.assertEqual(response.data["scope"]["project_id"], str(self.row.project_id))
        self.assertEqual(response.data["credential"]["mode"], "bearer_env")
        self.assertNotIn(self.token.key, str(response.data))
        self.assertNotIn("plaintext_key", response.data)

    def test_actual_output_snapshot_rescan_and_missing_snapshot_rejection(self):
        transfer = self.upload(b"safe output content", internal=True)
        artifact = transfer.artifact
        scan_path = self.history + f"outputs/{artifact.pk}/scan/"
        response = self.client.post(scan_path, {}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        runtime_services.finish_invocation_display_run(run=self.run, succeeded=True)
        response = self.client.get(self.history + "outputs/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row["id"] for row in response.data], [str(artifact.pk)])
        response = self.client.post(scan_path, {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        artifact.refresh_from_db()
        self.assertEqual((artifact.scan_status, artifact.policy_status), ("passed", "approved"))
        self.assertEqual(artifact.scan_metadata["pipeline"], "rules_stream_v2")
        artifact.snapshot_status = "pending"
        artifact.save(update_fields=["snapshot_status"])
        response = self.client.post(scan_path, {}, format="json")
        self.assertEqual(response.status_code, 400, response.data)

    def test_generated_image_artifacts_remain_caller_bound(self):
        transfer = self.upload(b"caller-owned snapshot", internal=True)
        artifact = transfer.artifact
        artifact.producer_step = "display_image:ownership-test"
        artifact.save(update_fields=["producer_step"])
        runtime_services.finish_invocation_display_run(run=self.run, succeeded=True)
        self.run.caller_subject_hash = "another-caller"
        self.run.save(update_fields=["caller_subject_hash"])
        self.assertEqual(self.client.get(self.history + "outputs/").data, [])
        response = self.client.post(self.history + f"outputs/{artifact.pk}/scan/", {}, format="json")
        self.assertEqual(response.status_code, 404, response.data)

    def test_mutations_require_csrf_and_foreign_agent_history_is_hidden(self):
        session = APIClient(enforce_csrf_checks=True)
        session.force_login(self.row.owner)
        self.assertEqual(session.post(self.history + "redact/", {}, format="json").status_code, 403)
        self.assertIn(APIClient().get(self.agent_base + "display-runs/").status_code, (401, 403))
        other = get_user_model().objects.create_user(username="other-history-owner")
        for changes in ({"created_by": other}, {"created_by": self.row.owner,
                         "project": Project.objects.create(tenant=self.row.tenant, name="Other history")}):
            for field, value in changes.items():
                setattr(self.agent, field, value)
            self.agent.save(update_fields=list(changes))
            for path in (self.agent_base + "logs/", self.agent_base + "status/",
                         self.agent_base + "display-runs/", self.history + "events/", self.history + "outputs/"):
                self.assertEqual(self.client.get(path).status_code, 404, path)
            self.assertEqual(self.client.post(self.history + "redact/", {}, format="json").status_code, 404)
