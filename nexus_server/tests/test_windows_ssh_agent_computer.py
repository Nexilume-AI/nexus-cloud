from __future__ import annotations

import os
import posixpath
from datetime import timedelta
from pathlib import Path
import unittest

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory

from apps.agents.models import Agent, AgentComputerBinding, AgentDisplayRun
from apps.agents.runtime_services import (
    finish_invocation_display_run,
    read_invocation_workspace_file,
    recall_invocation_memory,
    run_invocation_terminal_command,
    write_invocation_workspace_file,
)
from apps.agents.services import append_display_event
from apps.common.crypto import encrypt_secret
from apps.common.subjects import hash_token, request_subject
from apps.datasets.storage_backends import get_dataset_storage_backend
from apps.tenancy.models import Membership, Tenant
from apps.workspaces.models import WorkspaceConnection, WorkspaceTerminalSession
from apps.workspaces.services import WorkspaceError, WorkspaceNotFound, workspace_runner


WINDOWS_SSH_E2E = os.environ.get("NEXUS_WINDOWS_SSH_E2E") == "1"


@unittest.skipUnless(WINDOWS_SSH_E2E, "Set NEXUS_WINDOWS_SSH_E2E=1 to run the Windows SSH acceptance test.")
class WindowsSshAgentComputerAcceptanceTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.caller_a = user_model.objects.create_user(username="windows_ssh_caller_a", password="password")
        self.caller_b = user_model.objects.create_user(username="windows_ssh_caller_b", password="password")
        self.developer = user_model.objects.create_user(username="windows_ssh_developer", password="password")
        self.consumer = Tenant.objects.create(name="Windows SSH Consumer", slug="windows-ssh-consumer")
        self.producer = Tenant.objects.create(name="Windows SSH Producer", slug="windows-ssh-producer")
        Membership.objects.create(tenant=self.consumer, user=self.caller_a, role=Membership.ROLE_OWNER)
        Membership.objects.create(tenant=self.consumer, user=self.caller_b, role=Membership.ROLE_OWNER)
        Membership.objects.create(tenant=self.producer, user=self.developer, role=Membership.ROLE_OWNER)
        self.agent = Agent.objects.create(
            tenant=self.producer,
            name="WindowsSshHostedAgent",
            visibility=Agent.VISIBILITY_PUBLIC,
            publication_status=Agent.PUBLICATION_PUBLISHED,
            status=Agent.STATUS_ACTIVE,
            created_by=self.developer,
        )
        self.factory = APIRequestFactory()
        key_path = Path(os.environ["NEXUS_WINDOWS_SSH_PRIVATE_KEY"])
        self.private_key = key_path.read_text(encoding="utf-8")

    def _subject(self, user):
        request = self.factory.get("/", HTTP_X_NEXUS_TENANT=str(self.consumer.id))
        request.user = user
        request.tenant_id = str(self.consumer.id)
        return request_subject(request)

    def _binding(self, *, user, name: str, workspace_root: str) -> AgentComputerBinding:
        subject = self._subject(user)
        connection = WorkspaceConnection.objects.create(
            tenant=self.consumer,
            name=name,
            ssh_host=os.environ.get("NEXUS_WINDOWS_SSH_HOST", "127.0.0.1"),
            ssh_port=int(os.environ.get("NEXUS_WINDOWS_SSH_PORT", "22222")),
            ssh_user=os.environ.get("NEXUS_WINDOWS_SSH_USER", "CodexSandboxOffline"),
            auth_mode=WorkspaceConnection.AUTH_PRIVATE_KEY,
            encrypted_private_key=encrypt_secret(self.private_key),
            owner_subject_type=subject.principal_type,
            owner_subject_hash=subject.subject_hash,
            workspace_root=workspace_root,
            created_by=user,
        )
        probe = workspace_runner().test(connection=connection)
        self.assertTrue(probe.ok, probe.error)
        self.assertEqual(probe.facts.get("os"), "windows")
        connection.metadata = {"last_facts": probe.facts}
        connection.last_test_status = WorkspaceConnection.TEST_SUCCEEDED
        connection.save(update_fields=["metadata", "last_test_status", "updated_at"])
        return AgentComputerBinding.objects.create(
            tenant=self.consumer,
            agent=self.agent,
            connection=connection,
            caller_subject_hash=subject.subject_hash,
            caller_principal_type=subject.principal_type,
        )

    def _run(self, *, user, binding: AgentComputerBinding, label: str, display_token: str) -> AgentDisplayRun:
        subject = self._subject(user)
        run = AgentDisplayRun.objects.create(
            tenant=self.producer,
            consumer_tenant=self.consumer,
            agent=self.agent,
            computer_binding=binding,
            run_kind=AgentDisplayRun.KIND_INVOCATION,
            caller_principal_type=subject.principal_type,
            caller_principal_id=subject.principal_id,
            caller_subject_hash=subject.subject_hash,
            display_token_hash=hash_token(display_token),
            display_token_expires_at=timezone.now() + timedelta(hours=1),
            write_token=f"write-{label}",
            workspace_root=posixpath.join(binding.connection.workspace_root, "agents", str(self.agent.id), "workspace"),
            output_root=posixpath.join(
                binding.connection.workspace_root,
                "agents",
                str(self.agent.id),
                "runs",
                label,
                "outputs",
            ),
            title=label,
        )
        append_display_event(run=run, event_type="RUN_STARTED", payload={"runId": str(run.id)})
        return run

    def test_windows_ssh_multi_caller_private_computer_contract(self):
        storage_root = os.environ["NEXUS_WINDOWS_SSH_STORAGE_ROOT"]
        base_root = os.environ.get("NEXUS_WINDOWS_SSH_WORKSPACE_ROOT", "~/.nexus-e2e-20260813")
        with self.settings(
            NEXUS_WORKSPACE_SSH_RUNNER="paramiko",
            NEXUS_DATASET_STORAGE_BACKEND="local",
            NEXUS_DATASET_STORAGE_ROOT=storage_root,
        ):
            binding_a = self._binding(user=self.caller_a, name="caller-a-computer", workspace_root=f"{base_root}/caller-a")
            binding_b = self._binding(user=self.caller_b, name="caller-b-computer", workspace_root=f"{base_root}/caller-b")
            run_a1 = self._run(user=self.caller_a, binding=binding_a, label="a-1", display_token="display-a-1")
            run_a2 = self._run(user=self.caller_a, binding=binding_a, label="a-2", display_token="display-a-2")
            run_b1 = self._run(user=self.caller_b, binding=binding_b, label="b-1", display_token="display-b-1")

            write_invocation_workspace_file(
                run_id=str(run_a1.id), token=run_a1.write_token, path="state/shared.txt", content="caller-a-shared"
            )
            shared = read_invocation_workspace_file(
                run_id=str(run_a2.id), token=run_a2.write_token, path="state/shared.txt"
            )
            self.assertEqual(shared["content"], "caller-a-shared")
            with self.assertRaises(WorkspaceNotFound):
                read_invocation_workspace_file(
                    run_id=str(run_b1.id), token=run_b1.write_token, path="state/shared.txt"
                )
            with self.assertRaises(WorkspaceError):
                write_invocation_workspace_file(
                    run_id=str(run_a1.id), token=run_a1.write_token, path="../escape.txt", content="blocked"
                )

            terminal_a = run_invocation_terminal_command(
                run_id=str(run_a1.id), token=run_a1.write_token, command="Write-Output 'terminal-a'"
            )
            terminal_b = run_invocation_terminal_command(
                run_id=str(run_b1.id), token=run_b1.write_token, command="Write-Output 'terminal-b'"
            )
            self.assertEqual(terminal_a["exit_code"], 0, terminal_a)
            self.assertEqual(terminal_b["exit_code"], 0, terminal_b)
            self.assertIn("terminal-a", terminal_a["stdout"])
            self.assertIn("terminal-b", terminal_b["stdout"])
            session_a = WorkspaceTerminalSession.objects.get(display_run=run_a1)
            session_b = WorkspaceTerminalSession.objects.get(display_run=run_b1)
            self.assertNotEqual(session_a.id, session_b.id)
            transcript_a = "\n".join(session_a.transcript.values_list("data", flat=True))
            transcript_b = "\n".join(session_b.transcript.values_list("data", flat=True))
            self.assertIn("terminal-a", transcript_a)
            self.assertNotIn("terminal-b", transcript_a)
            self.assertIn("terminal-b", transcript_b)
            self.assertNotIn("terminal-a", transcript_b)

            append_display_event(
                run=run_a1,
                event_type="CUSTOM",
                payload={
                    "name": "nexus.memory.item",
                    "value": {"text": "caller-a-memory", "scope": "caller", "consent_status": "approved"},
                },
            )
            append_display_event(
                run=run_a2,
                event_type="CUSTOM",
                payload={
                    "name": "nexus.memory.item",
                    "value": {"text": "agent-global-memory", "scope": "agent_global", "consent_status": "approved"},
                },
            )
            recalled_a = recall_invocation_memory(run_id=str(run_a1.id), token=run_a1.write_token)
            recalled_b = recall_invocation_memory(run_id=str(run_b1.id), token=run_b1.write_token)
            self.assertEqual(
                {item["text"] for item in recalled_a["items"]},
                {"caller-a-memory", "agent-global-memory"},
            )
            self.assertEqual(
                {item["text"] for item in recalled_b["items"]},
                {"agent-global-memory"},
            )

            for run, content in [(run_a1, "output-from-a1"), (run_a2, "output-from-a2")]:
                write_invocation_workspace_file(
                    run_id=str(run.id),
                    token=run.write_token,
                    path="report.txt",
                    content=content,
                    root_kind="output",
                )
                append_display_event(
                    run=run,
                    event_type="CUSTOM",
                    payload={
                        "name": "nexus.file.created",
                        "value": {
                            "workspace_path": "report.txt",
                            "file_name": "report.txt",
                            "content_type": "text/plain",
                        },
                    },
                )

            finish_invocation_display_run(run=run_a1, succeeded=True)
            finish_invocation_display_run(run=run_a2, succeeded=True)
            artifact_a1 = run_a1.output_artifacts.get(workspace_path="report.txt")
            artifact_a2 = run_a2.output_artifacts.get(workspace_path="report.txt")
            self.assertEqual(artifact_a1.snapshot_status, "ready", artifact_a1.snapshot_error)
            self.assertEqual(artifact_a2.snapshot_status, "ready", artifact_a2.snapshot_error)
            self.assertNotEqual(artifact_a1.snapshot_object_key, artifact_a2.snapshot_object_key)
            self.assertNotEqual(artifact_a1.sha256, artifact_a2.sha256)
            backend = get_dataset_storage_backend()
            with backend.open(object_key=artifact_a1.snapshot_object_key) as handle:
                self.assertEqual(handle.read(), b"output-from-a1")
            with backend.open(object_key=artifact_a2.snapshot_object_key) as handle:
                self.assertEqual(handle.read(), b"output-from-a2")

            client = APIClient()
            client.force_authenticate(self.caller_a)
            own = client.get(
                f"/api/v1/agent-runs/{run_a1.id}/display/",
                HTTP_X_NEXUS_TENANT=str(self.consumer.id),
                HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN="display-a-1",
            )
            client.force_authenticate(self.caller_b)
            cross = client.get(
                f"/api/v1/agent-runs/{run_a1.id}/display/",
                HTTP_X_NEXUS_TENANT=str(self.consumer.id),
                HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN="display-a-1",
            )
            self.assertEqual(own.status_code, 200, own.content)
            self.assertEqual(cross.status_code, 404)

            self.assertEqual(list(run_a1.events.order_by("seq").values_list("seq", flat=True)), list(range(1, run_a1.events.count() + 1)))
            self.assertEqual(list(run_b1.events.order_by("seq").values_list("seq", flat=True)), [1])
