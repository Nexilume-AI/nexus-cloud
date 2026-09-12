"""Shared Run Computer-switch HTTP/state guards, not device execution proofs."""
import uuid
from django.utils import timezone
from rest_framework.test import APIRequestFactory
from apps.agents.models import AgentComputerBinding, AgentDisplayRun, AgentBrowserSession, AgentOutputArtifact
from apps.common.subjects import request_subject
from apps.workspaces.models import WorkspaceConnection, ComputerRuntimeDevice, WorkspaceTerminalSession


class RunComputerSwitchGuards:
    def setup_switch_state(self):
        req = APIRequestFactory().get("/")
        req.user, req.tenant_id, req.project_id = self.caller_a, str(self.tenant.pk), str(self.project_a.pk)
        self.subject = request_subject(req)
        self.agent.computer_requirement = "optional"
        self.agent.save(update_fields=["computer_requirement"])
        self.old = self.computer("Old Computer")
        self.new = self.computer("New Computer")
        self.binding = self.create_switch_binding(tenant=self.tenant, agent=self.agent,
            connection=self.old, caller_subject_hash=self.subject.subject_hash, is_default=True)
        self.run = AgentDisplayRun.objects.create(tenant=self.tenant, agent=self.agent,
            runtime=self.agent.runtime_deployments.first(), consumer_tenant=self.tenant,
            consumer_project=self.project_a, caller_subject_hash=self.subject.subject_hash,
            run_kind="invocation", status="completed", write_token="test-old-write",
            computer_binding=self.binding, workspace_root="old/workspace", output_root="old/outputs",
            workspace_cwd="previous-directory", workspace_capabilities_snapshot=["browser.control"],
            workspace_delegate_token_hash="old", browser_delegate_token_hash="old")
        self.headers = self._display_headers(str(self.run.pk))

    def computer(self, name):
        c = self.create_switch_connection(tenant=self.tenant, project=self.project_a,
            name=name, connection_type="runtime", owner_subject_hash=self.subject.subject_hash,
            owner_subject_type=self.subject.principal_type, workspace_root="~/.nexus")
        ComputerRuntimeDevice.objects.create(connection=c, public_key_fingerprint=uuid.uuid4().hex,
            platform="windows", capabilities={"workspace.v1": 1, "browser.v1": 1}, last_seen_at=timezone.now())
        return c

    def switch(self, connection=None, revision=0, headers=None):
        return self.client.post(f"/api/v1/agent-runs/{self.run.pk}/computer/",
            {"connection_id": str((connection or self.new).pk), "expected_revision": revision},
            format="json", **(headers or self.headers))

    def test_finished_run_switches_in_place_without_changing_default(self):
        response = self.switch()
        self.assertEqual(response.status_code, 200, response.content)
        self.run.refresh_from_db()
        self.assertEqual(self.run.computer_binding.connection_id, self.new.pk)
        self.assertEqual(self.run.workspace_cwd, ".")
        self.assertEqual(self.run.status, "completed")
        self.assertIn("computers/1/outputs", self.run.output_root)
        self.assertEqual(self.run.workspace_delegate_token_hash, "")
        self.binding.refresh_from_db()
        self.assertTrue(self.binding.is_default)

    def test_running_run_and_stale_tab_are_rejected(self):
        self.run.status = "running"
        self.run.save(update_fields=["status"])
        self.assertEqual(self.switch().status_code, 409)
        self.run.status = "failed"
        self.run.save(update_fields=["status"])
        self.assertEqual(self.switch().status_code, 200)
        self.assertEqual(self.switch(self.old).status_code, 409)

    def test_archives_sessions_and_keeps_old_output_snapshot(self):
        browser = AgentBrowserSession.objects.create(run=self.run, connection=self.old, status="closed")
        terminal = WorkspaceTerminalSession.objects.create(tenant=self.tenant, connection=self.old,
            display_run=self.run, session_kind="agent_run", computer_binding=self.binding, status="closed")
        artifact = AgentOutputArtifact.objects.create(tenant=self.tenant, agent=self.agent, run=self.run,
            workspace_path="report.md", original_file_name="report.md", snapshot_status="ready",
            snapshot_object_key="old-immutable-snapshot")
        self.assertEqual(self.switch().status_code, 200)
        browser.refresh_from_db(); terminal.refresh_from_db(); artifact.refresh_from_db()
        self.assertIsNone(browser.run_id)
        self.assertIsNone(terminal.display_run_id)
        historical = self.run.computer_attachments.get(revision=0)
        self.assertEqual(historical.browser_session_id, browser.pk)
        self.assertEqual(historical.terminal_session_id, terminal.pk)
        self.assertEqual(historical.binding_id, self.binding.pk)
        self.assertEqual(artifact.snapshot_object_key, "old-immutable-snapshot")
        self.run.refresh_from_db()
        from apps.agents.browser_runtime import _runtime_browser_session
        next_browser = _runtime_browser_session(run=self.run, viewport=(1280, 720))
        self.assertNotEqual(next_browser.pk, browser.pk)
        self.assertEqual(next_browser.connection_id, self.new.pk)
        from apps.agents.services import append_display_event
        self.run.status = "running"
        self.run.save(update_fields=["status"])
        append_display_event(run=self.run, event_type="CUSTOM", payload={"name":"nexus.file.created",
            "value":{"path":"report.md","content_type":"text/markdown"}})
        self.assertEqual(self.run.output_artifacts.filter(workspace_path="report.md").count(), 2)

    def test_noop_switch_preserves_sessions(self):
        self.assertEqual(self.switch(self.old).status_code, 200)
        self.run.refresh_from_db()
        self.assertEqual(self.run.computer_revision, 0)

    def test_resume_after_switch_uses_new_roots_and_preserves_chat(self):
        from apps.agents.models import AgentExecutionTask, AgentWorkspaceGrant, AgentTaskExecution
        from apps.agents.services import append_display_event
        from tests.agent_task_arguments import queued_arguments
        self.agent.workspace_capabilities = ["browser.control"]
        self.agent.save(update_fields=["workspace_capabilities"])
        AgentWorkspaceGrant.objects.create(tenant=self.tenant, project=self.project_a, agent=self.agent,
            caller_subject_hash=self.subject.subject_hash, scopes=["browser.control"])
        created = self._create("First question")
        self.assertEqual(created.status_code, 202, created.content)
        self.run = AgentDisplayRun.objects.get(pk=self.private_payload(created)["run_id"])
        self.headers = self._display_headers(str(self.run.pk))
        task = self.run.execution_task
        task.status = AgentExecutionTask.STATUS_COMPLETED
        task.save(update_fields=["status"])
        AgentTaskExecution.objects.filter(task=task).update(state="finished")
        append_display_event(run=self.run, event_type="RUN_FINISHED", payload={})
        # A finished lifecycle event alone is not enough while settlement is pending.
        self.assertEqual(self.switch().status_code, 409)
        self.run.runtime_invocations.filter(status="pending").update(status="success")
        changed = self.switch()
        self.assertEqual(changed.status_code, 200, changed.content)
        self.run.refresh_from_db()
        response = self.client.post(f"/api/v1/agent-runs/{self.run.pk}/resume/",
            {"content":"Continue on new Computer"}, format="json", **self.headers)
        self.assertEqual(response.status_code, 202, response.content)
        task.refresh_from_db()
        context = queued_arguments(task)["display_pair"][1]
        self.assertEqual(context.run_id, str(self.run.pk))
        self.assertEqual(context.workspace_root, self.run.workspace_root)
        self.assertEqual(context.output_root, self.run.output_root)
        self.assertTrue(context.browser_enabled)
        self.assertEqual(list(self.run.messages.filter(role="user").order_by("sequence").values_list("content", flat=True)),
            ["First question", "Continue on new Computer"])
        self.assertEqual(self.switch(self.old, revision=1).status_code, 409)

    def test_switch_does_not_replay_old_manifests_on_new_computer(self):
        from apps.agents.services import append_display_event, sync_output_artifacts_from_run
        self.run.status = "running"
        self.run.save(update_fields=["status"])
        append_display_event(run=self.run, event_type="CUSTOM", payload={"name":"nexus.file.created",
            "value":{"path":"old-only.md","content_type":"text/markdown"}})
        self.run.status = "completed"
        self.run.save(update_fields=["status"])
        self.assertEqual(self.switch().status_code, 200)
        self.run.refresh_from_db()
        sync_output_artifacts_from_run(run=self.run)
        self.assertFalse(self.run.output_artifacts.filter(computer_revision=1).exists())
