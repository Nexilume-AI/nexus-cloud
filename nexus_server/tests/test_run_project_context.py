from types import SimpleNamespace
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.agents.models import (
    Agent,
    AgentDisplayRun,
    AgentExecutionTask,
    AgentFileTransfer,
    AgentRunMessage,
)
from apps.agents.file_transfers import bind_files
from apps.agents.project_context import (
    AgentProjectContextPermissionRequired,
    authorize_project_context,
    ensure_run_project_context_authorized,
    project_context_snapshot,
    project_context_state,
    revoke_project_context,
)
from apps.agents.runtime_services import AgentRuntimeNotFound, get_run_context
from apps.agents.services import append_display_event
from apps.common.subjects import hash_token
from apps.tenancy.models import Membership, Project, Tenant


class RunProjectContextTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="project-context-owner", password="password"
        )
        self.consumer = Tenant.objects.create(name="Consumer", slug="project-context-consumer")
        self.publisher = Tenant.objects.create(name="Publisher", slug="project-context-publisher")
        Membership.objects.create(
            tenant=self.consumer, user=self.user, role=Membership.ROLE_OWNER
        )
        self.project = Project.objects.create(
            tenant=self.consumer,
            name="Research",
            instructions_markdown="Use **verified** sources.",
            instructions_revision=2,
            instructions_updated_at=timezone.now(),
        )
        self.owned_agent = Agent.objects.create(
            tenant=self.consumer, name="Owned Agent", created_by=self.user
        )
        self.external_agent = Agent.objects.create(
            tenant=self.publisher, name="External Agent", created_by=self.user
        )

    def request(self):
        return SimpleNamespace(
            user=self.user,
            tenant_id=str(self.consumer.id),
            project_id=str(self.project.id),
            api_key=None,
            headers={},
        )

    def test_project_instructions_revision_and_api_never_adds_a_placeholder(self):
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.patch(
            f"/api/v1/projects/{self.project.id}/",
            {"instructions_markdown": "Keep results concise."},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.consumer.id),
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["instructions_markdown"], "Keep results concise.")
        self.assertEqual(data["instructions_revision"], 3)

    def test_owned_agent_is_automatic_but_external_agent_needs_caller_grant(self):
        request = self.request()
        owned = project_context_snapshot(request=request, agent=self.owned_agent)
        self.assertEqual(owned["instructions_revision"], 2)

        state = project_context_state(request=request, agent=self.external_agent)
        self.assertTrue(state["requires_authorization"])
        with self.assertRaises(AgentProjectContextPermissionRequired):
            project_context_snapshot(request=request, agent=self.external_agent)

        allowed = authorize_project_context(request=request, agent=self.external_agent)
        self.assertTrue(allowed["authorized"])
        external = project_context_snapshot(request=request, agent=self.external_agent)
        self.assertEqual(external["instructions_markdown"], "Use **verified** sources.")

        run = SimpleNamespace(
            project_context_snapshot=external,
            agent=self.external_agent,
            consumer_tenant_id=self.consumer.id,
        )
        ensure_run_project_context_authorized(request=request, run=run)

        self.project.instructions_markdown = "Use reviewed sources and cite them."
        self.project.instructions_revision = 3
        self.project.save(update_fields=["instructions_markdown", "instructions_revision", "updated_at"])
        revised = project_context_state(request=request, agent=self.external_agent)
        self.assertTrue(revised["authorized"])
        self.assertEqual(revised["revision"], 3)
        self.assertEqual(run.project_context_snapshot["instructions_revision"], 2)

        revoke_project_context(request=request, agent=self.external_agent)
        with self.assertRaises(AgentProjectContextPermissionRequired):
            ensure_run_project_context_authorized(request=request, run=run)

    def test_run_context_uses_a_separate_token_and_returns_only_run_messages(self):
        token = "separate-context-token"
        run = AgentDisplayRun.objects.create(
            tenant=self.consumer,
            consumer_tenant=self.consumer,
            consumer_project=self.project,
            agent=self.owned_agent,
            run_kind=AgentDisplayRun.KIND_INVOCATION,
            caller_principal_type="user",
            caller_principal_id=str(self.user.id),
            caller_subject_hash="a" * 64,
            write_token="write-token",
            context_token_hash=hash_token(token),
            context_token_expires_at=timezone.now() + timedelta(minutes=5),
            project_context_snapshot={
                "project_id": str(self.project.id),
                "project_name": self.project.name,
                "instructions_markdown": self.project.instructions_markdown,
                "instructions_revision": 2,
                "captured_at": timezone.now().isoformat(),
            },
        )
        AgentRunMessage.objects.create(
            run=run,
            sequence=1,
            turn_index=1,
            role=AgentRunMessage.ROLE_USER,
            content="First turn",
        )
        data = get_run_context(run_id=str(run.id), token=token)
        self.assertEqual(data["project"]["revision"], 2)
        self.assertEqual(data["messages"][0]["turn_index"], 1)
        self.assertEqual(data["messages"][0]["content"], "First turn")
        with self.assertRaises(AgentRuntimeNotFound):
            get_run_context(run_id=str(run.id), token="wrong-token")

    def test_new_attachments_and_outputs_keep_turn_lineage(self):
        run = AgentDisplayRun.objects.create(
            tenant=self.consumer,
            consumer_tenant=self.consumer,
            consumer_project=self.project,
            agent=self.owned_agent,
            run_kind=AgentDisplayRun.KIND_INVOCATION,
            caller_subject_hash="b" * 64,
            write_token="write-token",
        )
        AgentExecutionTask.objects.create(
            run=run,
            agent=self.owned_agent,
            caller_subject_hash=run.caller_subject_hash,
            tool_name="inspect",
            request_json={"turn_index": 3},
            expires_at=timezone.now() + timedelta(hours=1),
        )
        transfer = AgentFileTransfer.objects.create(
            tenant=self.consumer,
            project=self.project,
            agent=self.owned_agent,
            caller_subject_hash=run.caller_subject_hash,
            direction="input",
            name="brief.md",
            content_type="text/markdown",
            size_bytes=10,
            state="ready",
            storage_backend="local",
            expires_at=timezone.now() + timedelta(hours=1),
        )
        bind_files(run=run, rows=[transfer], turn_index=3)
        transfer.refresh_from_db()
        self.assertEqual(transfer.turn_index, 3)

        append_display_event(
            run=run,
            event_type="CUSTOM",
            payload={
                "type": "CUSTOM",
                "name": "nexus.file.created",
                "value": {"path": "outputs/report.md", "content_type": "text/markdown"},
            },
        )
        artifact = run.output_artifacts.get(workspace_path="report.md")
        self.assertEqual(artifact.turn_index, 3)
