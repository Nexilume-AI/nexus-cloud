from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import base64
import hashlib
from io import BytesIO
import tempfile
import threading
import unittest
import uuid
from unittest import mock
from PIL import Image

from django.contrib.auth import get_user_model
from django.db import connection, close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework import exceptions

from apps.agents.models import Agent, AgentComputerBinding, AgentDisplayAsset, AgentDisplayRun, AgentRunCheckpoint, AgentRunInteraction, AgentRunMessage, AgentWorkspaceGrant
from apps.agents.runtime_services import (
    AgentComputerRequired,
    finish_invocation_display_run,
    resolve_invocation_computer_binding,
    run_invocation_terminal_command,
    write_invocation_workspace_file,
    invocation_workspace_relative_path,
)
from apps.agents.services import append_display_event, get_public_display_run
from apps.common.subjects import hash_token, request_subject
from apps.datasets.storage_backends import get_dataset_storage_backend
from apps.tenancy.models import Membership, Tenant
from apps.workspaces.models import WorkspaceConnection, WorkspaceTerminalSession, WorkspaceTerminalTranscript


class PrivateAgentRunIsolationTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user_a = user_model.objects.create_user(username="caller_a", password="password")
        self.user_b = user_model.objects.create_user(username="caller_b", password="password")
        self.developer = user_model.objects.create_user(username="developer", password="password")
        self.tenant = Tenant.objects.create(name="Consumer", slug="consumer-private-runs")
        self.producer = Tenant.objects.create(name="Producer", slug="producer-private-runs")
        Membership.objects.create(tenant=self.tenant, user=self.user_a, role=Membership.ROLE_OWNER)
        Membership.objects.create(tenant=self.tenant, user=self.user_b, role=Membership.ROLE_OWNER)
        Membership.objects.create(tenant=self.producer, user=self.developer, role=Membership.ROLE_OWNER)
        self.agent = Agent.objects.create(
            tenant=self.producer,
            name="SharedHostedAgent",
            visibility=Agent.VISIBILITY_PUBLIC,
            publication_status=Agent.PUBLICATION_PUBLISHED,
            status=Agent.STATUS_ACTIVE,
            created_by=self.developer,
        )
        self.factory = APIRequestFactory()
        self._media = tempfile.TemporaryDirectory()
        self._media_settings = override_settings(MEDIA_ROOT=self._media.name)
        self._media_settings.enable()
        self.addCleanup(self._media_settings.disable)
        self.addCleanup(self._media.cleanup)

    def _subject(self, user):
        request = self.factory.get("/", HTTP_X_NEXUS_TENANT=str(self.tenant.id))
        request.user = user
        request.tenant_id = str(self.tenant.id)
        return request_subject(request)

    def _run(self, user, token):
        subject = self._subject(user)
        run = AgentDisplayRun.objects.create(
            tenant=self.producer,
            consumer_tenant=self.tenant,
            agent=self.agent,
            run_kind=AgentDisplayRun.KIND_INVOCATION,
            caller_principal_type=subject.principal_type,
            caller_principal_id=subject.principal_id,
            caller_subject_hash=subject.subject_hash,
            display_token_hash=hash_token(token),
            display_token_expires_at=timezone.now() + timedelta(hours=1),
            write_token="write-" + token,
            title="private invocation",
        )
        append_display_event(run=run, event_type="STEP_STARTED", payload={"stepName": "isolated"})
        return run

    def test_display_requires_matching_identity_and_token(self):
        run_a = self._run(self.user_a, "token-a")
        self._run(self.user_b, "token-b")
        client = APIClient()
        client.force_authenticate(self.user_a)
        path = f"/api/v1/agent-runs/{run_a.id}/display/"

        ok = client.get(path, HTTP_X_NEXUS_TENANT=str(self.tenant.id), HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN="token-a")
        wrong_token = client.get(path, HTTP_X_NEXUS_TENANT=str(self.tenant.id), HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN="token-b")
        client.force_authenticate(self.user_b)
        wrong_user = client.get(path, HTTP_X_NEXUS_TENANT=str(self.tenant.id), HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN="token-a")

        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(wrong_token.status_code, 404)
        self.assertEqual(wrong_user.status_code, 404)

    def test_terminal_transcript_is_paginated_and_returns_viewer_context(self):
        run = self._run(self.user_a, "token-a")
        subject = self._subject(self.user_a)
        connection = WorkspaceConnection.objects.create(
            tenant=self.tenant,
            owner_subject_type=subject.principal_type,
            owner_subject_hash=subject.subject_hash,
            workspace_root="~/.nexus",
            name="Caller workstation",
            connection_type="runtime",
            created_by=self.user_a,
        )
        session = WorkspaceTerminalSession.objects.create(
            tenant=self.tenant,
            connection=connection,
            display_run=run,
            session_kind=WorkspaceTerminalSession.KIND_AGENT_RUN,
            status=WorkspaceTerminalSession.STATUS_ACTIVE,
            shell=WorkspaceTerminalSession.SHELL_POWERSHELL,
            last_error="",
            started_at=timezone.now(),
        )
        WorkspaceTerminalTranscript.objects.bulk_create([
            WorkspaceTerminalTranscript(
                session=session,
                seq=index,
                kind=WorkspaceTerminalTranscript.KIND_STDOUT,
                command_id="command-1",
                data=f"line-{index}\n",
            )
            for index in range(1, 502)
        ])
        client = APIClient()
        client.force_authenticate(self.user_a)
        headers = {
            "HTTP_X_NEXUS_TENANT": str(self.tenant.id),
            "HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN": "token-a",
        }

        first = client.get(f"/api/v1/agent-runs/{run.id}/terminal/?cursor=0", **headers)
        second = client.get(f"/api/v1/agent-runs/{run.id}/terminal/?cursor=500", **headers)

        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(len(first.json()["data"]["events"]), 500)
        self.assertTrue(first.json()["data"]["has_more"])
        self.assertEqual(first.json()["data"]["next_cursor"], 500)
        self.assertEqual(first.json()["data"]["computer_name"], "Caller workstation")
        self.assertEqual(first.json()["data"]["shell"], "powershell")
        self.assertEqual(first.json()["data"]["viewer_mode"], "read_only")
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual([row["seq"] for row in second.json()["data"]["events"]], [501])
        self.assertFalse(second.json()["data"]["has_more"])

    def test_private_events_include_protected_browser_frame_only_for_run_caller(self):
        run = self._run(self.user_a, "token-a")
        append_display_event(
            run=run,
            event_type="CUSTOM",
            payload={
                "name": "nexus.computer.frame",
                "value": {
                    "screenshot_url": f"/api/v1/agent-runs/{run.id}/display-assets/{uuid.uuid4()}/",
                    "source": "attached_computer",
                },
            },
            visibility="private",
        )
        path = f"/api/v1/agent-runs/{run.id}/events/?cursor=0"
        client = APIClient()
        client.force_authenticate(self.user_a)

        response = client.get(
            path,
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
            HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN="token-a",
        )

        self.assertEqual(response.status_code, 200, response.content)
        names = [item.get("name") for item in response.json()["data"]]
        self.assertIn("nexus.computer.frame", names)
        self.assertIn("STEP_STARTED", [item["type"] for item in response.json()["data"]])

        client.force_authenticate(self.user_b)
        denied = client.get(
            path,
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
            HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN="token-a",
        )
        self.assertEqual(denied.status_code, 404)

    def test_public_display_never_selects_invocation_run(self):
        invocation = self._run(self.user_a, "token-a")
        public_run = get_public_display_run(agent_id=str(self.agent.id))
        self.assertNotEqual(public_run.id, invocation.id)
        self.assertEqual(public_run.run_kind, AgentDisplayRun.KIND_DEMO)

    def _runtime_directory_reply(self, **kwargs):
        self.assertEqual(kwargs["operation"], "workspace.list_files")
        self.assertEqual(kwargs["required_scope"], "files.list")
        self.assertEqual(kwargs["connection"].connection_type, WorkspaceConnection.TYPE_RUNTIME)
        root = kwargs["payload"]["workspace_root"]
        path = kwargs["payload"]["path"]
        self.assertIn(path, (root, root + "/documents"))
        return {"items": ([{"name": "documents", "relative_path": "documents", "type": "directory"}]
                          if path == root else [{"name": "input.txt", "type": "file"}])}

    @mock.patch("apps.workspaces.computer_runtime.execute_runtime_command")
    def test_caller_can_browse_and_change_completed_run_working_folder(self, execute):
        execute.side_effect = self._runtime_directory_reply
        run = self._run(self.user_a, "token-a")
        subject = self._subject(self.user_a)
        root = f"~/.nexus/agents/{self.agent.id}/workspace"
        connection = WorkspaceConnection.objects.create(
            tenant=self.tenant,
            owner_subject_type=subject.principal_type,
            owner_subject_hash=subject.subject_hash,
            workspace_root="~/.nexus",
            name="Caller computer",
            connection_type=WorkspaceConnection.TYPE_RUNTIME,
            created_by=self.user_a,
        )
        binding = AgentComputerBinding.objects.create(
            tenant=self.tenant,
            agent=self.agent,
            connection=connection,
            caller_subject_hash=subject.subject_hash,
            caller_principal_type=subject.principal_type,
        )
        run.computer_binding = binding
        run.workspace_root = root
        run.status = AgentDisplayRun.STATUS_COMPLETED
        run.completed_at = timezone.now()
        run.save(update_fields=["computer_binding", "workspace_root", "status", "completed_at", "updated_at"])

        client = APIClient()
        client.force_authenticate(self.user_a)
        headers = {
            "HTTP_X_NEXUS_TENANT": str(self.tenant.id),
            "HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN": "token-a",
        }
        listing = client.get(f"/api/v1/agent-runs/{run.id}/computer/", **headers)
        self.assertEqual(listing.status_code, 200, listing.content)
        self.assertEqual(listing.json()["data"]["directories"], [{"name": "documents", "path": "documents"}])

        changed = client.patch(
            f"/api/v1/agent-runs/{run.id}/computer/",
            {"path": "documents"},
            format="json",
            **headers,
        )
        self.assertEqual(changed.status_code, 200, changed.content)
        run.refresh_from_db()
        self.assertEqual(run.workspace_cwd, "documents")
        self.assertEqual(
            invocation_workspace_relative_path(run=run, path="input.txt", root_kind="workspace"),
            "documents/input.txt",
        )
        self.assertEqual(
            invocation_workspace_relative_path(run=run, path="report.txt", root_kind="output"),
            "report.txt",
        )

        escaped = client.patch(
            f"/api/v1/agent-runs/{run.id}/computer/",
            {"path": "../../outside"},
            format="json",
            **headers,
        )
        self.assertEqual(escaped.status_code, 400)
        self.assertEqual(execute.call_count, 3)

    @mock.patch("apps.workspaces.computer_runtime.execute_runtime_command")
    def test_running_run_can_change_working_folder_for_subsequent_operations(self, execute):
        execute.side_effect = self._runtime_directory_reply
        run = self._run(self.user_a, "token-a")
        subject = self._subject(self.user_a)
        root = f"~/.nexus/agents/{self.agent.id}/workspace"
        connection = WorkspaceConnection.objects.create(
            tenant=self.tenant,
            owner_subject_type=subject.principal_type,
            owner_subject_hash=subject.subject_hash,
            name="Busy computer",
            connection_type=WorkspaceConnection.TYPE_RUNTIME,
            workspace_root="~/.nexus",
            created_by=self.user_a,
        )
        binding = AgentComputerBinding.objects.create(
            tenant=self.tenant,
            agent=self.agent,
            connection=connection,
            caller_subject_hash=subject.subject_hash,
            caller_principal_type=subject.principal_type,
        )
        run.computer_binding = binding
        run.workspace_root = root
        run.save(update_fields=["computer_binding", "workspace_root", "updated_at"])
        client = APIClient()
        client.force_authenticate(self.user_a)
        response = client.patch(
            f"/api/v1/agent-runs/{run.id}/computer/",
            {"path": "documents"},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
            HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN="token-a",
        )
        self.assertEqual(response.status_code, 200, response.content)
        run.refresh_from_db()
        self.assertEqual(run.workspace_cwd, "documents")
        self.assertEqual(execute.call_count, 2)

    def test_closed_invocation_rejects_late_events(self):
        run = self._run(self.user_a, "token-a")
        append_display_event(run=run, event_type="RUN_FINISHED", payload={})
        with self.assertRaises(exceptions.ValidationError):
            append_display_event(run=run, event_type="STEP_FINISHED", payload={"stepName": "late"})

    def test_interactive_chat_reply_resumes_only_the_owning_run(self):
        run = self._run(self.user_a, "token-a")
        run.interaction_token_hash = hash_token("interaction-a")
        run.interaction_token_expires_at = timezone.now() + timedelta(hours=1)
        run.interaction_mode = "stream"
        run.save(update_fields=["interaction_token_hash", "interaction_token_expires_at", "interaction_mode"])
        internal = APIClient()
        created = internal.post(
            f"/api/v1/internal/agent-runs/{run.id}/interactions/",
            {
                "key": "confirm-save",
                "prompt": "Continue and save?",
                "kind": "select",
                "choices": [{"value": "continue", "label": "Continue"}, {"value": "cancel", "label": "Cancel"}],
                "timeout_seconds": 300,
            },
            format="json",
            HTTP_X_NEXUS_INTERACTION_TOKEN="interaction-a",
        )
        self.assertEqual(created.status_code, 201, created.content)
        interaction_id = created.json()["data"]["id"]

        caller = APIClient()
        caller.force_authenticate(self.user_a)
        answered = caller.post(
            f"/api/v1/agent-runs/{run.id}/interactions/{interaction_id}/reply/",
            {"value": "continue", "text": "Continue"},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
            HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN="token-a",
        )
        self.assertEqual(answered.status_code, 200, answered.content)
        current = internal.get(
            f"/api/v1/internal/agent-runs/{run.id}/interactions/{interaction_id}/",
            HTTP_X_NEXUS_INTERACTION_TOKEN="interaction-a",
        )
        self.assertEqual(current.json()["data"]["response"]["value"], "continue")
        self.assertEqual(run.interactions.get().status, AgentRunInteraction.STATUS_ANSWERED)
        self.assertEqual(run.events.filter(event_type="TEXT_MESSAGE_CONTENT").count(), 1)
        reply = AgentRunMessage.objects.get(run=run, role=AgentRunMessage.ROLE_USER)
        self.assertEqual(reply.content, "Continue")
        self.assertEqual(reply.content_blocks, [{"type": "markdown", "text": "Continue"}])

        follow_up = internal.post(
            f"/api/v1/internal/agent-runs/{run.id}/interactions/",
            {"key": "next-command", "prompt": "What next?", "kind": "text", "timeout_seconds": 300},
            format="json",
            HTTP_X_NEXUS_INTERACTION_TOKEN="interaction-a",
        )
        self.assertEqual(follow_up.status_code, 201, follow_up.content)
        second_answer = caller.post(
            f"/api/v1/agent-runs/{run.id}/interactions/{follow_up.json()['data']['id']}/reply/",
            {"value": "show files", "text": "show files"},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
            HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN="token-a",
        )
        self.assertEqual(second_answer.status_code, 200, second_answer.content)
        self.assertEqual(
            list(
                AgentRunMessage.objects.filter(run=run, role=AgentRunMessage.ROLE_USER)
                .order_by("sequence")
                .values_list("content", flat=True)
            ),
            ["Continue", "show files"],
        )

        display = caller.get(
            f"/api/v1/agent-runs/{run.id}/display/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
            HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN="token-a",
        )
        self.assertEqual(display.status_code, 200, display.content)
        self.assertEqual(
            [item["content"] for item in display.json()["data"]["messages"][-2:]],
            ["Continue", "show files"],
        )

        caller.force_authenticate(self.user_b)
        denied = caller.post(
            f"/api/v1/agent-runs/{run.id}/interactions/{interaction_id}/reply/",
            {"text": "steal"},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
            HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN="token-a",
        )
        self.assertEqual(denied.status_code, 404)

    def test_interaction_stable_key_checkpoint_and_protected_browser_asset(self):
        run = self._run(self.user_a, "token-a")
        run.interaction_token_hash = hash_token("interactive-secret")
        run.interaction_token_expires_at = timezone.now() + timedelta(hours=1)
        run.interaction_mode = "stream"
        run.save(update_fields=["interaction_token_hash", "interaction_token_expires_at", "interaction_mode"])
        internal = APIClient()
        interaction_path = f"/api/v1/internal/agent-runs/{run.id}/interactions/"
        first = internal.post(interaction_path, {"key": "stable", "prompt": "One?"}, format="json", HTTP_X_NEXUS_INTERACTION_TOKEN="interactive-secret")
        second = internal.post(interaction_path, {"key": "stable", "prompt": "One?"}, format="json", HTTP_X_NEXUS_INTERACTION_TOKEN="interactive-secret")
        self.assertEqual(first.json()["data"]["id"], second.json()["data"]["id"])
        self.assertEqual(AgentRunInteraction.objects.filter(run=run).count(), 1)

        checkpoint_path = f"/api/v1/internal/agent-runs/{run.id}/checkpoint/"
        saved = internal.put(checkpoint_path, {"stage": "confirmed", "data": {"choice": "continue"}}, format="json", HTTP_X_NEXUS_INTERACTION_TOKEN="interactive-secret")
        updated = internal.put(checkpoint_path, {"stage": "saved", "data": {"done": True}, "expected_revision": 1}, format="json", HTTP_X_NEXUS_INTERACTION_TOKEN="interactive-secret")
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual(updated.json()["data"]["revision"], 2)
        self.assertEqual(AgentRunCheckpoint.objects.get(run=run).stage, "saved")

        invalid_png = b"\x89PNG\r\n\x1a\n" + b"nexus-frame"
        rejected = internal.post(
            f"/api/v1/internal/agent-runs/{run.id}/display-assets/",
            {"content_type": "image/png", "content_base64": base64.b64encode(invalid_png).decode(),
             "sha256": hashlib.sha256(invalid_png).hexdigest()},
            format="json", HTTP_X_NEXUS_INTERACTION_TOKEN="interactive-secret",
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertFalse(AgentDisplayAsset.objects.filter(run=run).exists())
        image = BytesIO()
        Image.new("RGB", (2, 2), "white").save(image, format="PNG")
        png = image.getvalue()
        uploaded = internal.post(
            f"/api/v1/internal/agent-runs/{run.id}/display-assets/",
            {"content_type": "image/png", "content_base64": base64.b64encode(png).decode(), "sha256": hashlib.sha256(png).hexdigest()},
            format="json",
            HTTP_X_NEXUS_INTERACTION_TOKEN="interactive-secret",
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.content)
        asset = AgentDisplayAsset.objects.get(run=run)
        caller = APIClient()
        caller.force_authenticate(self.user_a)
        download = caller.get(
            uploaded.json()["data"]["url"],
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
            HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN="token-a",
        )
        self.assertEqual(download.status_code, 200)
        # Let Django's streaming wrapper close the response without closing the
        # enclosing TestCase transaction via an unwrapped request_finished signal.
        self.assertEqual(b"".join(download.streaming_content), png)
        self.assertTrue(download.closed)
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            self.assertEqual(cursor.fetchone()[0], 1)
        caller.force_authenticate(self.user_b)
        denied = caller.get(
            f"/api/v1/agent-runs/{run.id}/display-assets/{asset.id}/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
            HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN="token-a",
        )
        self.assertEqual(denied.status_code, 404)

    def test_workspace_connections_are_owner_filtered(self):
        subject_a = self._subject(self.user_a)
        WorkspaceConnection.objects.create(
            tenant=self.tenant,
            name="a-computer",
            ssh_host="a.internal",
            ssh_user="a",
            owner_subject_type=subject_a.principal_type,
            owner_subject_hash=subject_a.subject_hash,
        )
        client = APIClient()
        client.force_authenticate(self.user_b)
        response = client.get("/api/v1/workspace-connections/", HTTP_X_NEXUS_TENANT=str(self.tenant.id))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"], [])

    def test_api_key_external_users_have_distinct_hmac_subjects(self):
        def subject(external_id):
            request = self.factory.get("/", HTTP_X_NEXUS_END_USER=external_id)
            request.api_key = SimpleNamespace(id="key-1", tenant_id=self.tenant.id)
            request.user = SimpleNamespace(is_authenticated=True, id="principal")
            request.tenant_id = str(self.tenant.id)
            return request_subject(request)

        first = subject("customer-a@example.invalid")
        second = subject("customer-b@example.invalid")
        self.assertNotEqual(first.subject_hash, second.subject_hash)
        self.assertEqual(first.principal_id, "key-1")
        self.assertNotIn("customer-a", repr(first))

    def test_computer_requirement_is_resolved_before_a_run_exists(self):
        request = self.factory.get("/", HTTP_X_NEXUS_TENANT=str(self.tenant.id))
        request.user = self.user_a
        request.tenant_id = str(self.tenant.id)
        request.project_id = ""
        self.agent.computer_requirement = Agent.COMPUTER_REQUIRED
        self.agent.save(update_fields=["computer_requirement"])
        before = AgentDisplayRun.objects.count()
        with self.assertRaises(AgentComputerRequired):
            resolve_invocation_computer_binding(request=request, agent=self.agent, tenant=self.tenant)
        self.assertEqual(AgentDisplayRun.objects.count(), before)

    def test_disabled_agent_ignores_owned_binding_header(self):
        subject = self._subject(self.user_a)
        connection = WorkspaceConnection.objects.create(
            tenant=self.tenant,
            name="disabled-computer",
            ssh_host="disabled.internal",
            ssh_user="caller",
            owner_subject_type=subject.principal_type,
            owner_subject_hash=subject.subject_hash,
        )
        binding = AgentComputerBinding.objects.create(
            tenant=self.tenant,
            agent=self.agent,
            connection=connection,
            caller_subject_hash=subject.subject_hash,
            caller_principal_type=subject.principal_type,
        )
        request = self.factory.get(
            "/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
            HTTP_X_NEXUS_AGENT_COMPUTER_BINDING=str(binding.id),
        )
        request.user = self.user_a
        request.tenant_id = str(self.tenant.id)
        self.agent.computer_requirement = Agent.COMPUTER_DISABLED
        self.agent.save(update_fields=["computer_requirement"])
        self.assertIsNone(resolve_invocation_computer_binding(request=request, agent=self.agent, tenant=self.tenant))

    def _runtime_terminal_reply(self, **kwargs):
        self.assertEqual(kwargs["connection"].connection_type, WorkspaceConnection.TYPE_RUNTIME)
        self.assertTrue(kwargs["display_run_id"])
        if kwargs["operation"] == "workspace.ensure_directory":
            self.assertEqual(kwargs["required_scope"], "files.write")
            return {}
        self.assertEqual(kwargs["operation"], "command.execute")
        self.assertEqual(kwargs["required_scope"], "command.execute")
        return {"stdout": kwargs["payload"]["command"].removeprefix("echo ") + "\n", "stderr": "", "exit_code": 0}

    @mock.patch("apps.workspaces.computer_runtime.execute_runtime_command")
    def test_concurrent_runs_use_separate_terminal_sessions_on_shared_workspace(self, execute):
        execute.side_effect = self._runtime_terminal_reply
        subject = self._subject(self.user_a)
        self.agent.workspace_capabilities = ["command.execute"]
        self.agent.save(update_fields=["workspace_capabilities"])
        AgentWorkspaceGrant.objects.create(
            tenant=self.tenant,
            agent=self.agent,
            caller_subject_hash=subject.subject_hash,
            caller_principal_type=subject.principal_type,
            scopes=["command.execute"],
            granted_at=timezone.now(),
        )
        connection = WorkspaceConnection.objects.create(
            tenant=self.tenant,
            name="shared-computer",
            connection_type=WorkspaceConnection.TYPE_RUNTIME,
            workspace_root="~/.nexus",
            owner_subject_type=subject.principal_type,
            owner_subject_hash=subject.subject_hash,
        )
        binding = AgentComputerBinding.objects.create(
            tenant=self.tenant,
            agent=self.agent,
            connection=connection,
            caller_subject_hash=subject.subject_hash,
            caller_principal_type=subject.principal_type,
        )
        runs = []
        for index in range(2):
            delegate_token = f"terminal-delegate-{index}"
            run = AgentDisplayRun.objects.create(
                tenant=self.producer,
                consumer_tenant=self.tenant,
                agent=self.agent,
                computer_binding=binding,
                run_kind=AgentDisplayRun.KIND_INVOCATION,
                caller_subject_hash=subject.subject_hash,
                write_token=f"terminal-{index}",
                workspace_capabilities_snapshot=["command.execute"],
                workspace_delegate_token_hash=hash_token(delegate_token),
                workspace_delegate_token_expires_at=timezone.now() + timedelta(hours=1),
                workspace_root=f"~/.nexus/agents/{self.agent.id}/workspace",
                output_root=f"~/.nexus/agents/{self.agent.id}/runs/run-{index}/outputs",
            )
            run_invocation_terminal_command(run_id=str(run.id), token=delegate_token, command=f"echo run-{index}")
            runs.append(run)
        sessions = list(WorkspaceTerminalSession.objects.filter(display_run__in=runs).order_by("created_at"))
        self.assertEqual(len(sessions), 2)
        self.assertEqual(runs[0].workspace_root, runs[1].workspace_root)
        self.assertNotEqual(runs[0].output_root, runs[1].output_root)
        self.assertNotEqual(sessions[0].id, sessions[1].id)
        self.assertIn("run-0", "\n".join(sessions[0].transcript.values_list("data", flat=True)))
        self.assertNotIn("run-1", "\n".join(sessions[0].transcript.values_list("data", flat=True)))
        self.assertEqual(execute.call_count, 4)
        self.assertEqual({call.kwargs["display_run_id"] for call in execute.call_args_list}, {str(run.id) for run in runs})

    @mock.patch("apps.workspaces.computer_runtime.execute_runtime_command")
    def test_terminal_command_can_skip_private_display_transcript(self, execute):
        execute.side_effect = self._runtime_terminal_reply
        subject = self._subject(self.user_a)
        self.agent.workspace_capabilities = ["command.execute"]
        self.agent.save(update_fields=["workspace_capabilities"])
        AgentWorkspaceGrant.objects.create(
            tenant=self.tenant,
            agent=self.agent,
            caller_subject_hash=subject.subject_hash,
            caller_principal_type=subject.principal_type,
            scopes=["command.execute"],
            granted_at=timezone.now(),
        )
        connection = WorkspaceConnection.objects.create(
            tenant=self.tenant,
            name="hidden-command-computer",
            connection_type=WorkspaceConnection.TYPE_RUNTIME,
            workspace_root="~/.nexus",
            owner_subject_type=subject.principal_type,
            owner_subject_hash=subject.subject_hash,
        )
        binding = AgentComputerBinding.objects.create(
            tenant=self.tenant,
            agent=self.agent,
            connection=connection,
            caller_subject_hash=subject.subject_hash,
            caller_principal_type=subject.principal_type,
        )
        delegate_token = "hidden-terminal-delegate"
        run = AgentDisplayRun.objects.create(
            tenant=self.producer,
            consumer_tenant=self.tenant,
            agent=self.agent,
            computer_binding=binding,
            run_kind=AgentDisplayRun.KIND_INVOCATION,
            caller_subject_hash=subject.subject_hash,
            write_token="hidden-terminal",
            workspace_capabilities_snapshot=["command.execute"],
            workspace_delegate_token_hash=hash_token(delegate_token),
            workspace_delegate_token_expires_at=timezone.now() + timedelta(hours=1),
            workspace_root=f"~/.nexus/agents/{self.agent.id}/workspace",
            output_root=f"~/.nexus/agents/{self.agent.id}/runs/hidden/outputs",
        )

        result = run_invocation_terminal_command(
            run_id=str(run.id),
            token=delegate_token,
            command="echo hidden",
            display=False,
        )

        self.assertFalse(result["displayed"])
        self.assertFalse(WorkspaceTerminalSession.objects.filter(display_run=run).exists())
        self.assertEqual(execute.call_count, 2)

    @mock.patch("apps.workspaces.computer_runtime.execute_runtime_command", return_value={"written": True})
    def test_output_write_snapshots_acknowledged_bytes_before_run_completion(self, execute):
        subject = self._subject(self.user_a)
        self.agent.workspace_capabilities = ["files.write"]
        self.agent.save(update_fields=["workspace_capabilities"])
        AgentWorkspaceGrant.objects.create(
            tenant=self.tenant,
            agent=self.agent,
            caller_subject_hash=subject.subject_hash,
            caller_principal_type=subject.principal_type,
            scopes=["files.write"],
            granted_at=timezone.now(),
        )
        connection = WorkspaceConnection.objects.create(
            tenant=self.tenant,
            name="output-computer",
            connection_type=WorkspaceConnection.TYPE_RUNTIME,
            workspace_root="~/.nexus",
            owner_subject_type=subject.principal_type,
            owner_subject_hash=subject.subject_hash,
        )
        binding = AgentComputerBinding.objects.create(
            tenant=self.tenant,
            agent=self.agent,
            connection=connection,
            caller_subject_hash=subject.subject_hash,
            caller_principal_type=subject.principal_type,
        )
        delegate_token = "output-delegate"
        run = AgentDisplayRun.objects.create(
            tenant=self.producer,
            consumer_tenant=self.tenant,
            agent=self.agent,
            computer_binding=binding,
            run_kind=AgentDisplayRun.KIND_INVOCATION,
            caller_subject_hash=subject.subject_hash,
            write_token="output-write",
            workspace_capabilities_snapshot=["files.write"],
            workspace_delegate_token_hash=hash_token(delegate_token),
            workspace_delegate_token_expires_at=timezone.now() + timedelta(hours=1),
            workspace_root=f"~/.nexus/agents/{self.agent.id}/workspace",
            output_root=f"~/.nexus/agents/{self.agent.id}/runs/output/outputs",
        )
        rendered = '{"status":"completed"}\n'
        with self.settings(
            NEXUS_DATASET_STORAGE_BACKEND="local",
            NEXUS_DATASET_STORAGE_ROOT=self._media.name,
        ):
            write_invocation_workspace_file(
                run_id=str(run.id),
                token=delegate_token,
                path="result.json",
                content=rendered,
                root_kind="output",
            )
            artifact = run.output_artifacts.get(workspace_path="result.json")
            self.assertEqual(artifact.snapshot_status, "ready", artifact.snapshot_error)
            first_object_key = artifact.snapshot_object_key
            with get_dataset_storage_backend().open(object_key=first_object_key) as handle:
                self.assertEqual(handle.read(), rendered.encode("utf-8"))

            append_display_event(
                run=run,
                event_type="CUSTOM",
                payload={
                    "name": "nexus.file.created",
                    "value": {
                        "workspace_path": "result.json",
                        "original_file_name": "result.json",
                        "content_type": "application/json",
                    },
                },
            )
            finish_invocation_display_run(run=run, succeeded=True)
            artifact.refresh_from_db()
            self.assertEqual(artifact.snapshot_status, "ready")
            self.assertEqual(artifact.snapshot_object_key, first_object_key)
            self.assertEqual(artifact.content_type, "application/json")
            self.assertEqual(execute.call_count, 1)
            self.assertEqual(execute.call_args.kwargs["operation"], "workspace.write_file")
            self.assertEqual(execute.call_args.kwargs["required_scope"], "files.write")
            self.assertEqual(execute.call_args.kwargs["payload"]["content"], rendered)
            self.assertEqual(execute.call_args.kwargs["display_run_id"], str(run.id))


@unittest.skipUnless(connection.vendor == "postgresql", "PostgreSQL concurrency contract")
class PrivateAgentRunPostgresConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        # Real database concurrency, with only the outbound device transport
        # mocked. Never enable legacy SSH or launch a host command in this suite.
        self.runtime_execute = self.enterContext(mock.patch(
            "apps.workspaces.computer_runtime.execute_runtime_command",
            side_effect=self.execute_device_command,
        ))
        self.producer = Tenant.objects.create(name="Concurrent Producer", slug="concurrent-producer")
        self.consumer = Tenant.objects.create(name="Concurrent Consumer", slug="concurrent-consumer")
        self.agent = Agent.objects.create(
            tenant=self.producer,
            name="ConcurrentAgent",
            visibility=Agent.VISIBILITY_PUBLIC,
            publication_status=Agent.PUBLICATION_PUBLISHED,
            status=Agent.STATUS_ACTIVE,
            workspace_capabilities=["command.execute"],
        )
        self.bindings = []
        for caller_index in range(4):
            subject_hash = f"{caller_index:064x}"
            computer = WorkspaceConnection.objects.create(
                tenant=self.consumer,
                name=f"computer-{caller_index}",
                connection_type=WorkspaceConnection.TYPE_RUNTIME,
                workspace_root="~/.nexus",
                owner_subject_type="user",
                owner_subject_hash=subject_hash,
            )
            self.bindings.append(
                AgentComputerBinding.objects.create(
                    tenant=self.consumer,
                    agent=self.agent,
                    connection=computer,
                    caller_subject_hash=subject_hash,
                    caller_principal_type="user",
                )
            )
            AgentWorkspaceGrant.objects.create(
                tenant=self.consumer,
                agent=self.agent,
                caller_subject_hash=subject_hash,
                caller_principal_type="user",
                scopes=["command.execute"],
                granted_at=timezone.now(),
            )

    def execute_device_command(self, **kwargs):
        run = AgentDisplayRun.objects.select_related("computer_binding").get(
            id=kwargs["display_run_id"],
        )
        self.assertEqual(kwargs["connection"].id, run.computer_binding.connection_id)
        self.assertEqual(kwargs["caller_subject_hash"], run.caller_subject_hash)
        operation = kwargs["operation"]
        if operation == "workspace.ensure_directory":
            self.assertEqual(kwargs["required_scope"], "files.write")
            return {}
        self.assertEqual(operation, "command.execute")
        self.assertEqual(kwargs["required_scope"], "command.execute")
        self.assertTrue(kwargs["idempotency_key"])
        return {"stdout": kwargs["payload"]["command"], "stderr": "", "exit_code": 0}

    def test_four_callers_create_100_isolated_runs_traces_and_terminals(self):
        tasks = [(caller, call) for caller in range(4) for call in range(25)]

        def invoke(item):
            caller, call = item
            close_old_connections()
            binding = AgentComputerBinding.objects.select_related("connection").get(id=self.bindings[caller].id)
            delegate_token = f"delegate-{caller}-{call}"
            run = AgentDisplayRun.objects.create(
                tenant=self.producer,
                consumer_tenant=self.consumer,
                agent=self.agent,
                computer_binding=binding,
                run_kind=AgentDisplayRun.KIND_INVOCATION,
                caller_principal_type="user",
                caller_principal_id=str(caller),
                caller_subject_hash=binding.caller_subject_hash,
                write_token=f"run-{caller}-{call}",
                workspace_capabilities_snapshot=["command.execute"],
                workspace_delegate_token_hash=hash_token(delegate_token),
                workspace_delegate_token_expires_at=timezone.now() + timedelta(hours=1),
                display_token_hash=hash_token(f"display-{caller}-{call}"),
                display_token_expires_at=timezone.now() + timedelta(hours=1),
                workspace_root=f"~/.nexus/agents/{self.agent.id}/workspace",
                output_root=f"~/.nexus/agents/{self.agent.id}/runs/{caller}-{call}/outputs",
            )
            append_display_event(run=run, event_type="STEP_STARTED", payload={"stepName": f"caller-{caller}"})
            run_invocation_terminal_command(run_id=str(run.id), token=delegate_token, command=f"echo {caller}-{call}")
            append_display_event(run=run, event_type="STEP_FINISHED", payload={"stepName": f"caller-{caller}"})
            close_old_connections()
            return run.id

        with ThreadPoolExecutor(max_workers=12) as executor:
            run_ids = list(executor.map(invoke, tasks))

        self.assertEqual(len(set(run_ids)), 100)
        self.assertEqual(AgentDisplayRun.objects.filter(id__in=run_ids).count(), 100)
        self.assertEqual(WorkspaceTerminalSession.objects.filter(display_run_id__in=run_ids).count(), 100)
        self.assertEqual(self.runtime_execute.call_count, 200)
        for run in AgentDisplayRun.objects.filter(id__in=run_ids):
            self.assertEqual(list(run.events.order_by("seq").values_list("seq", flat=True)), [1, 2])
            self.assertTrue(run.output_root.endswith("/outputs"))
            session = WorkspaceTerminalSession.objects.get(display_run=run)
            self.assertEqual(session.connection_id, run.computer_binding.connection_id)
            stdout = session.transcript.get(kind=WorkspaceTerminalTranscript.KIND_STDOUT)
            self.assertTrue(stdout.data.startswith(f"echo {run.caller_principal_id}-"))

    def test_workspace_switch_does_not_wait_for_dispatched_terminal_command(self):
        binding = self.bindings[0]
        delegate_token = "delegate-folder-switch"
        run = AgentDisplayRun.objects.create(
            tenant=self.producer,
            consumer_tenant=self.consumer,
            agent=self.agent,
            computer_binding=binding,
            run_kind=AgentDisplayRun.KIND_INVOCATION,
            caller_principal_type="user",
            caller_principal_id="0",
            caller_subject_hash=binding.caller_subject_hash,
            write_token="run-folder-switch",
            workspace_capabilities_snapshot=["command.execute"],
            workspace_delegate_token_hash=hash_token(delegate_token),
            workspace_delegate_token_expires_at=timezone.now() + timedelta(hours=1),
            workspace_root=f"~/.nexus/agents/{self.agent.id}/workspace",
            output_root=f"~/.nexus/agents/{self.agent.id}/runs/folder-switch/outputs",
            workspace_cwd=".",
        )
        started = threading.Event()
        release = threading.Event()
        captured_cwds = []

        def slow_command(**kwargs):
            captured_cwds.append(kwargs["cwd"])
            started.set()
            if not release.wait(5):
                raise TimeoutError("terminal fixture was not released")
            return {"stdout": "done", "stderr": "", "exit_code": 0}

        def switch_folder():
            close_old_connections()
            try:
                AgentDisplayRun.objects.filter(id=run.id).update(workspace_cwd="documents")
            finally:
                close_old_connections()

        with mock.patch("apps.workspaces.execution.run_workspace_command", side_effect=slow_command):
            with ThreadPoolExecutor(max_workers=2) as executor:
                command_future = executor.submit(
                    run_invocation_terminal_command,
                    run_id=str(run.id),
                    token=delegate_token,
                    command="first",
                )
                self.assertTrue(started.wait(2), "terminal command did not start")
                switch_future = executor.submit(switch_folder)
                try:
                    switch_future.result(timeout=2)
                finally:
                    release.set()
                self.assertEqual(command_future.result(timeout=5)["exit_code"], 0)

        with mock.patch(
            "apps.workspaces.execution.run_workspace_command",
            return_value={"stdout": "next", "stderr": "", "exit_code": 0},
        ) as next_command:
            run_invocation_terminal_command(
                run_id=str(run.id),
                token=delegate_token,
                command="second",
            )

        self.assertEqual(captured_cwds, ["."])
        self.assertEqual(next_command.call_args.kwargs["cwd"], "documents")
