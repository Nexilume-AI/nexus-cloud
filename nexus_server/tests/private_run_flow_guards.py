"""Original Private Run HTTP/state guards; transport mocks are not live Agent proof."""
from __future__ import annotations

import json
import time
from unittest import mock

from rest_framework.test import APIClient, APIRequestFactory

from apps.agents.models import (
    Agent,
    AgentComputerBinding,
    AgentDisplayRun,
    AgentExecutionTask,
    AgentRunMessage,
    AgentRuntimeDeployment,
    AgentRuntimeImage,
    AgentVersion,
    AgentWorkspaceGrant,
)
from apps.common.subjects import request_subject
from apps.agents.runtime_runner import RuntimeMCPResult
from apps.agents.services import append_display_event, materialize_private_run_result
from apps.tenancy.models import Project
from apps.workspaces.models import WorkspaceConnection, ComputerRuntimeDevice
from tests.agent_task_arguments import queued_arguments


class PrivateRunFlowGuards:
    def _headers(self, project: Project | None = None) -> dict[str, str]:
        return {
            "HTTP_X_NEXUS_TENANT": str(self.tenant.id),
            **({"HTTP_X_NEXUS_PROJECT": str(project.id)} if project else {}),
        }

    def _create(self, content: str, project: Project | None = None):
        with mock.patch("apps.agents.runtime_services.threading.Thread.start"):
            return self.client.post(
                f"/api/v1/agents/{self.agent.id}/private-runs/",
                {"content": content},
                format="json",
                **self._headers(project or self.project_a),
            )

    def _wait(self, run_id: str, timeout: float = 5.0) -> AgentDisplayRun:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = AgentDisplayRun.objects.get(id=run_id)
            if run.status != AgentDisplayRun.STATUS_RUNNING:
                return run
            time.sleep(0.05)
        self.fail("Private Run did not finish")

    def _display_headers(self, run_id: str, project: Project | None = None) -> dict[str, str]:
        selected = project or self.project_a
        response = self.client.post(
            f"/api/v1/agent-runs/{run_id}/display-token/",
            {},
            format="json",
            **self._headers(selected),
        )
        self.assertEqual(response.status_code, 200, response.content)
        return {
            **self._headers(selected),
            "HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN": self.private_payload(response)["display_token"],
        }

    def test_unicode_input_creates_independent_run_message(self) -> None:
        self.authenticate_private_caller()
        response = self._create("中文老师🙂，请检查工作区。")
        self.assertEqual(response.status_code, 202, response.content)
        payload = self.private_payload(response)
        self.assertNotIn("thread", payload)
        self.assertIn(f"?run={payload['run_id']}", payload["display_url"])
        message = AgentRunMessage.objects.get(run_id=payload["run_id"], role="user")
        self.assertEqual(message.content, "中文老师🙂，请检查工作区。")
        self.assertEqual(message.content_blocks, [{"type": "markdown", "text": message.content}])

    def test_each_top_level_input_creates_a_separate_run(self) -> None:
        self.authenticate_private_caller()
        first = self._create("first task")
        second = self._create("second task")
        self.assertEqual(first.status_code, 202, first.content)
        self.assertEqual(second.status_code, 202, second.content)
        run_ids = {self.private_payload(first)["run_id"], self.private_payload(second)["run_id"]}
        self.assertEqual(len(run_ids), 2)
        self.assertEqual(AgentExecutionTask.objects.filter(run_id__in=run_ids).count(), 2)
        self.assertEqual(
            set(AgentRunMessage.objects.filter(run_id__in=run_ids, role="user").values_list("content", flat=True)),
            {"first task", "second task"},
        )

    def test_private_display_rejects_structured_mcp_only_tool(self) -> None:
        self.authenticate_private_caller()
        self.agent.repo_metadata = {
            "tools": [
                {
                    "name": "browser_session",
                    "title": "Browser session",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "format": "uri"},
                            "actions": {"type": "array"},
                        },
                        "required": ["url"],
                        "additionalProperties": False,
                    },
                }
            ]
        }
        self.agent.save(update_fields=["repo_metadata", "updated_at"])
        self.version.tool_runtime_policy = {
            "browser_session": {"task": True, "interactive": True}
        }
        self.version.save(update_fields=["tool_runtime_policy", "updated_at"])

        missing = self.client.post(
            f"/api/v1/agents/{self.agent.id}/private-runs/",
            {"content": "open example", "tool_name": "browser_session"},
            format="json",
            **self._headers(self.project_a),
        )
        self.assertEqual(missing.status_code, 404, missing.content)
        self.assertEqual(AgentDisplayRun.objects.count(), 0)

        with mock.patch("apps.agents.runtime_services.threading.Thread") as thread_type:
            rejected = self.client.post(
                f"/api/v1/agents/{self.agent.id}/private-runs/",
                {
                    "tool_name": "browser_session",
                    "arguments": {
                        "url": "https://example.com/",
                        "actions": [],
                    },
                },
                format="json",
                **self._headers(self.project_a),
            )

        self.assertEqual(rejected.status_code, 404, rejected.content)
        self.assertEqual(AgentDisplayRun.objects.count(), 0)
        thread_type.assert_not_called()

    def test_non_continuable_tool_rejects_follow_up_turn(self) -> None:
        self.authenticate_private_caller()
        self.version.tool_runtime_policy = {
            "inspect": {"task": True, "interactive": True, "continuable": False, "chat": True}
        }
        self.version.save(update_fields=["tool_runtime_policy", "updated_at"])
        created = self._create("one-shot request")
        self.assertEqual(created.status_code, 202, created.content)
        run_id = self.private_payload(created)["run_id"]
        run = AgentDisplayRun.objects.get(id=run_id)
        task = run.execution_task
        task.status = AgentExecutionTask.STATUS_FAILED
        task.error_code = "HANDLER_FAILED"
        task.completed_at = run.started_at
        task.save(update_fields=["status", "error_code", "completed_at", "updated_at"])

        with mock.patch("apps.agents.runtime_services.threading.Thread") as thread_type:
            resumed = self.client.post(
                f"/api/v1/agent-runs/{run_id}/resume/",
                {"content": "try again"},
                format="json",
                **self._display_headers(run_id),
            )

        self.assertEqual(resumed.status_code, 409, resumed.content)
        self.assertFalse(run.messages.filter(content="try again").exists())
        thread_type.assert_not_called()

    def test_completed_run_can_resume_same_sdk_context_without_new_history_item(self) -> None:
        self.authenticate_private_caller()
        created = self._create("first question")
        self.assertEqual(created.status_code, 202, created.content)
        run_id = self.private_payload(created)["run_id"]
        run = AgentDisplayRun.objects.get(id=run_id)
        self.assertEqual(run.project_context_snapshot["instructions_revision"], 1)
        self.project_a.instructions_markdown = "A later revision"
        self.project_a.instructions_revision = 2
        self.project_a.save(update_fields=["instructions_markdown", "instructions_revision", "updated_at"])
        task = run.execution_task
        original_task_id = task.id
        original_write_token = run.write_token
        task.status = AgentExecutionTask.STATUS_COMPLETED
        task.completed_at = run.started_at
        task.save(update_fields=["status", "completed_at", "updated_at"])
        append_display_event(
            run=run,
            event_type="RUN_FINISHED",
            payload={"runId": run_id, "threadId": str(self.agent.id)},
        )

        with mock.patch("apps.agents.runtime_services.threading.Thread") as thread_type:
            resumed = self.client.post(
                f"/api/v1/agent-runs/{run_id}/resume/",
                {"content": "second question"},
                format="json",
                **self._display_headers(run_id),
            )

        self.assertEqual(resumed.status_code, 202, resumed.content)
        self.assertEqual(self.private_payload(resumed)["run_id"], run_id)
        self.assertTrue(self.private_payload(resumed)["resumed"])
        self.assertEqual(AgentDisplayRun.objects.filter(id=run_id).count(), 1)
        run.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(run.status, AgentDisplayRun.STATUS_RUNNING)
        self.assertIsNone(run.completed_at)
        self.assertNotEqual(run.write_token, original_write_token)
        self.assertEqual(task.id, original_task_id)
        self.assertEqual(task.status, AgentExecutionTask.STATUS_WORKING)
        self.assertEqual(task.request_json["turn_index"], 2)
        self.assertEqual(
            list(run.messages.filter(role="user").order_by("sequence").values_list("content", flat=True)),
            ["first question", "second question"],
        )
        self.assertEqual(run.events.filter(event_type="RUN_STARTED").count(), 2)
        worker_values = queued_arguments(task)
        resumed_context = worker_values["display_pair"][1]
        self.assertEqual(resumed_context.run_id, run_id)
        self.assertEqual(resumed_context.turn_index, 2)
        request_body = json.loads(worker_values["body"].decode("utf-8"))
        self.assertEqual(request_body["params"]["arguments"], {"message": "second question"})
        history = APIClient().get(
            f"/api/v1/internal/agent-runs/{run_id}/context/",
            HTTP_X_NEXUS_CONTEXT_TOKEN=resumed_context.context_token,
        )
        self.assertEqual(history.status_code, 200, history.content)
        self.assertEqual(
            [item["content"] for item in self.private_payload(history)["messages"]],
            ["first question", "second question"],
        )
        self.assertEqual(self.private_payload(history)["project"]["revision"], 1)
        self.assertEqual([item["turn_index"] for item in self.private_payload(history)["messages"]], [1, 2])

    def test_lost_runtime_recovers_the_same_turn_without_manual_resume(self) -> None:
        self.authenticate_private_caller()
        subject_request = APIRequestFactory().get(
            "/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
            HTTP_X_NEXUS_PROJECT=str(self.project_a.id),
        )
        subject_request.user = self.caller_a
        subject_request.tenant_id = str(self.tenant.id)
        subject_request.project_id = str(self.project_a.id)
        subject = request_subject(subject_request)
        self.agent.workspace_capabilities = ["files.read", "command.execute"]
        self.agent.save(update_fields=["workspace_capabilities", "updated_at"])
        AgentWorkspaceGrant.objects.create(
            tenant=self.tenant,
            project=self.project_a,
            agent=self.agent,
            caller_subject_hash=subject.subject_hash,
            caller_principal_type=subject.principal_type,
            scopes=["files.read", "command.execute"],
        )
        connection = self.create_private_connection(
            tenant=self.tenant,
            name="recovery-computer",
            connection_type="runtime",
            ssh_host="recovery.internal",
            ssh_user="caller",
            owner_subject_type=subject.principal_type,
            owner_subject_hash=subject.subject_hash,
        )
        from django.utils import timezone
        ComputerRuntimeDevice.objects.create(connection=connection, public_key_fingerprint="recovery-test",
            platform="windows", last_seen_at=timezone.now(), capabilities={"workspace.v1": 1, "terminal.v1": 1})
        binding = self.create_private_binding(
            tenant=self.tenant,
            agent=self.agent,
            connection=connection,
            caller_subject_hash=subject.subject_hash,
            caller_principal_type=subject.principal_type,
        )
        with mock.patch("apps.agents.runtime_services.threading.Thread") as initial_thread:
            created = self.client.post(
                f"/api/v1/agents/{self.agent.id}/private-runs/",
                {"content": "before disconnect"},
                format="json",
                HTTP_X_NEXUS_AGENT_COMPUTER_BINDING=str(binding.id),
                **self._headers(self.project_a),
            )
        self.assertEqual(created.status_code, 202, created.content)
        run_id = self.private_payload(created)["run_id"]
        run = AgentDisplayRun.objects.get(id=run_id)
        task = run.execution_task
        runtime = run.runtime
        original_write_token = run.write_token
        original_delegate_hash = run.workspace_delegate_token_hash
        run.workspace_cwd = "documents"
        run.save(update_fields=["workspace_cwd", "updated_at"])
        from apps.agents import task_execution as durable_worker
        first_lease = durable_worker.claim()
        with mock.patch("apps.agents.runtime_services.initialize_private_runtime_mcp_session", return_value={}), \
             mock.patch("apps.agents.runtime_services.close_private_runtime_mcp_session"), \
             mock.patch("apps.agents.runtime_services.call_runtime_mcp", side_effect=ConnectionError("connection lost")):
            durable_worker.execute(*first_lease)
        run.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(run.status, AgentDisplayRun.STATUS_RUNNING)
        self.assertEqual(task.status, AgentExecutionTask.STATUS_WAITING_FOR_RUNTIME)
        self.assertEqual(task.execution.state, "waiting_for_runtime")
        self.assertTrue(task.execution.encrypted_payload)
        runtime.status = AgentRuntimeDeployment.STATUS_FAILED
        runtime.health_status = AgentRuntimeDeployment.HEALTH_UNHEALTHY
        runtime.last_error = "connection lost"
        runtime.save(update_fields=["status", "health_status", "last_error", "updated_at"])
        # Simulate the connector's next successful Registration/Presence renewal.
        runtime.status = AgentRuntimeDeployment.STATUS_ACTIVE
        runtime.health_status = AgentRuntimeDeployment.HEALTH_HEALTHY
        runtime.last_error = ""
        runtime.save(update_fields=["status", "health_status", "last_error", "updated_at"])

        self.assertEqual(durable_worker.reconcile_waiting(), 1)
        result = RuntimeMCPResult(
            status_code=200,
            headers={},
            body=b'{"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"recovered"}]}}',
        )
        replacement_lease = durable_worker.claim()
        self.assertNotEqual(replacement_lease[1], first_lease[1])
        with mock.patch("apps.agents.runtime_services.initialize_private_runtime_mcp_session", return_value={}), \
             mock.patch("apps.agents.runtime_services.close_private_runtime_mcp_session"), \
             mock.patch("apps.agents.runtime_services.call_runtime_mcp", return_value=result):
            durable_worker.execute(*replacement_lease)
        run.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(run.runtime_id, runtime.id)
        self.assertEqual(task.runtime_id, runtime.id)
        self.assertEqual(task.request_json["turn_index"], 1)
        self.assertEqual(task.status, AgentExecutionTask.STATUS_COMPLETED)
        self.assertEqual(run.workspace_cwd, "documents")
        self.assertNotEqual(run.write_token, original_write_token)
        self.assertNotEqual(run.workspace_delegate_token_hash, original_delegate_hash)
        history = self.client.get(
            f"/api/v1/agent-runs/{run_id}/display/",
            **self._display_headers(run_id),
        )
        self.assertEqual(history.status_code, 200, history.content)
        history_items = self.private_payload(history)["messages"]
        self.assertEqual(
            [item["content"] for item in history_items if item["role"] == "user"],
            ["before disconnect"],
        )
        self.assertNotIn("Agent runtime invocation failed.", [
            item["content"] for item in history_items if item["role"] == "assistant"
        ])

    def test_failed_run_rebinds_to_replacement_runtime(self) -> None:
        self.authenticate_private_caller()
        created = self._create("before runtime replacement")
        self.assertEqual(created.status_code, 202, created.content)
        run = AgentDisplayRun.objects.get(id=self.private_payload(created)["run_id"])
        task = run.execution_task
        old_runtime = run.runtime
        task.status = AgentExecutionTask.STATUS_FAILED
        task.error_code = "OPENWRT_CONNECTION_LOST"
        task.completed_at = run.started_at
        task.save(update_fields=["status", "error_code", "completed_at", "updated_at"])
        append_display_event(
            run=run,
            event_type="RUN_ERROR",
            payload={"runId": str(run.id), "message": "connection lost"},
        )
        old_runtime.status = AgentRuntimeDeployment.STATUS_FAILED
        old_runtime.health_status = AgentRuntimeDeployment.HEALTH_UNHEALTHY
        old_runtime.env = "retired"
        old_runtime.save(update_fields=["status", "health_status", "env", "updated_at"])
        replacement = self.create_private_runtime(
            tenant=self.tenant,
            agent=self.agent,
            image=old_runtime.image,
            runtime_kind=old_runtime.runtime_kind,
            status=AgentRuntimeDeployment.STATUS_ACTIVE,
            health_status=AgentRuntimeDeployment.HEALTH_HEALTHY,
            container_id="replacement_run_agent",
            internal_mcp_url="http://replacement-agent-runtime.local/mcp",
        )

        with mock.patch("apps.agents.runtime_services.threading.Thread"):
            resumed = self.client.post(
                f"/api/v1/agent-runs/{run.id}/resume/",
                {"content": "continue on replacement"},
                format="json",
                **self._display_headers(str(run.id)),
            )

        self.assertEqual(resumed.status_code, 202, resumed.content)
        run.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(run.runtime_id, replacement.id)
        self.assertEqual(task.runtime_id, replacement.id)
        self.assertEqual(task.request_json["turn_index"], 2)

    def test_failed_run_rejects_next_turn_while_runtime_is_offline(self) -> None:
        self.authenticate_private_caller()
        created = self._create("before offline resume")
        self.assertEqual(created.status_code, 202, created.content)
        run = AgentDisplayRun.objects.get(id=self.private_payload(created)["run_id"])
        task = run.execution_task
        task.status = AgentExecutionTask.STATUS_FAILED
        task.error_code = "OPENWRT_CONNECTION_LOST"
        task.completed_at = run.started_at
        task.save(update_fields=["status", "error_code", "completed_at", "updated_at"])
        append_display_event(
            run=run,
            event_type="RUN_ERROR",
            payload={"runId": str(run.id), "message": "connection lost"},
        )
        run.runtime.status = AgentRuntimeDeployment.STATUS_FAILED
        run.runtime.health_status = AgentRuntimeDeployment.HEALTH_UNHEALTHY
        run.runtime.save(update_fields=["status", "health_status", "updated_at"])

        response = self.client.post(
            f"/api/v1/agent-runs/{run.id}/resume/",
            {"content": "too early"},
            format="json",
            **self._display_headers(str(run.id)),
        )

        self.assertEqual(response.status_code, 503, response.content)
        task.refresh_from_db()
        self.assertEqual(task.status, AgentExecutionTask.STATUS_FAILED)
        self.assertFalse(run.messages.filter(content="too early").exists())

    def test_history_search_rename_and_soft_delete(self) -> None:
        self.authenticate_private_caller()
        response = self._create("find the private report")
        run_id = self.private_payload(response)["run_id"]
        renamed = self.client.patch(
            f"/api/v1/agent-runs/{run_id}/display/",
            {"title": "Workspace report"},
            format="json",
            **self._display_headers(run_id),
        )
        self.assertEqual(renamed.status_code, 200, renamed.content)
        listed = self.client.get(
            f"/api/v1/agents/{self.agent.id}/private-runs/?q=Workspace",
            **self._headers(self.project_a),
        )
        self.assertEqual(listed.status_code, 200, listed.content)
        self.assertEqual(self.private_payload(listed)["results"][0]["id"], run_id)
        # Removing history must not hide a still-running quota consumer.
        stopped = self.client.post(f"/api/v1/agent-runs/{run_id}/cancel/", {}, format="json", **self._display_headers(run_id))
        self.assertEqual(stopped.status_code, 200, stopped.content)
        deleted = self.client.delete(f"/api/v1/agent-runs/{run_id}/display/", **self._display_headers(run_id))
        self.assertEqual(deleted.status_code, 204, deleted.content)
        self.assertIsNotNone(AgentDisplayRun.objects.get(id=run_id).caller_hidden_at)
        missing = self.client.get(f"/api/v1/agents/{self.agent.id}/private-runs/", **self._headers(self.project_a))
        self.assertFalse(any(item["id"] == run_id for item in self.private_payload(missing)["results"]))

    def test_agent_title_then_user_title_precedence(self) -> None:
        self.authenticate_private_caller()
        response = self._create("inspect")
        run = AgentDisplayRun.objects.get(id=self.private_payload(response)["run_id"])
        append_display_event(run=run, event_type="CUSTOM", payload={"name": "nexus.display.title", "value": {"title": "Agent title"}})
        append_display_event(run=run, event_type="CUSTOM", payload={"name": "nexus.display.title", "value": {"title": "Second Agent title"}})
        run.refresh_from_db()
        self.assertEqual((run.display_title, run.display_title_source), ("Agent title", "agent"))
        self.client.patch(f"/api/v1/agent-runs/{run.id}/display/", {"title": "My title"}, format="json", **self._display_headers(str(run.id)))
        append_display_event(run=run, event_type="CUSTOM", payload={"name": "nexus.display.title", "value": {"title": "Late title"}})
        run.refresh_from_db()
        self.assertEqual((run.display_title, run.display_title_source), ("My title", "user"))

    def test_multiple_agent_messages_and_result_merge_into_one_response(self) -> None:
        self.authenticate_private_caller()
        response = self._create("inspect")
        run = AgentDisplayRun.objects.get(id=self.private_payload(response)["run_id"])
        for message_id, content in (("say-1", "First update"), ("say-2", "Second update")):
            append_display_event(run=run, event_type="TEXT_MESSAGE_START", payload={"messageId": message_id, "role": "assistant"})
            append_display_event(run=run, event_type="TEXT_MESSAGE_CONTENT", payload={"messageId": message_id, "delta": content})
            append_display_event(run=run, event_type="TEXT_MESSAGE_END", payload={"messageId": message_id})
        materialize_private_run_result(run=run, value={"structuredContent": {"files": 3}})
        responses = AgentRunMessage.objects.filter(run=run, role="assistant")
        self.assertEqual(responses.count(), 1)
        response_message = responses.get()
        self.assertEqual(response_message.content, "First update\n\nSecond update")
        self.assertIn({"type": "fields", "items": [{"label": "Files", "value": "3"}]}, response_message.content_blocks)

    def test_failed_result_does_not_append_success_fallback_to_agent_error(self) -> None:
        self.authenticate_private_caller()
        response = self._create("open the browser")
        run = AgentDisplayRun.objects.get(id=self.private_payload(response)["run_id"])
        append_display_event(
            run=run,
            event_type="TEXT_MESSAGE_START",
            payload={"messageId": "browser-error", "role": "assistant"},
        )
        append_display_event(
            run=run,
            event_type="TEXT_MESSAGE_CONTENT",
            payload={"messageId": "browser-error", "delta": "The browser action could not be completed."},
        )
        append_display_event(
            run=run,
            event_type="TEXT_MESSAGE_END",
            payload={"messageId": "browser-error"},
        )

        materialize_private_run_result(run=run, value={}, succeeded=False)

        response_message = AgentRunMessage.objects.get(run=run, role=AgentRunMessage.ROLE_ASSISTANT)
        self.assertEqual(response_message.content, "The browser action could not be completed.")
        self.assertNotIn("Agent completed the task.", response_message.content)

    def test_assistant_messages_are_grouped_only_until_the_next_user_reply(self) -> None:
        self.authenticate_private_caller()
        response = self._create("inspect")
        run = AgentDisplayRun.objects.get(id=self.private_payload(response)["run_id"])

        def message(message_id: str, role: str, content: str) -> None:
            append_display_event(run=run, event_type="TEXT_MESSAGE_START", payload={"messageId": message_id, "role": role})
            append_display_event(run=run, event_type="TEXT_MESSAGE_CONTENT", payload={"messageId": message_id, "delta": content})
            append_display_event(run=run, event_type="TEXT_MESSAGE_END", payload={"messageId": message_id})

        message("assistant-1", "assistant", "First answer")
        message("assistant-2", "assistant", "More detail")
        message("user-2", "user", "Now show files")
        message("assistant-3", "assistant", "Here are the files")
        message("assistant-4", "assistant", "Two files found")
        materialize_private_run_result(run=run, value={"structuredContent": {"files": 2}})

        messages = list(AgentRunMessage.objects.filter(run=run).order_by("sequence"))
        self.assertEqual([item.role for item in messages], ["user", "assistant", "user", "assistant"])
        self.assertEqual(
            [item.content for item in messages],
            ["inspect", "First answer\n\nMore detail", "Now show files", "Here are the files\n\nTwo files found"],
        )
        self.assertIn(
            {"type": "fields", "items": [{"label": "Files", "value": "2"}]},
            messages[-1].content_blocks,
        )
