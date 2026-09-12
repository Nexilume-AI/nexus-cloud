"""Fresh real execution tables; NOT device execution or delegate authorization E2E."""
from datetime import timedelta
import uuid

from django.apps import apps
from django.db import connection, IntegrityError, transaction
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.state import ProjectState
from django.test import TestCase
from django.utils import timezone

from apps.agents.models import (
    Agent, AgentBrowserSession, AgentComputerBinding, AgentDisplayEvent,
    AgentDisplayRun, AgentExecutionTask, AgentMobileBinding, AgentOutputArtifact,
    AgentRunComputerAttachment, AgentRunMessage, AgentRuntimeInvocation,
)
from apps.mobile.models import MobileCommand, MobileDevice
from apps.workspaces.models import (
    ComputerRuntimeCommand, ComputerRuntimeDevice, WorkspaceConnection,
    WorkspaceTerminalSession, WorkspaceTerminalTranscript,
)
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


class PersonalRuntimeModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.installation = provision_owner(email="runtime-owner@example.test", password=PASSWORD)
        cls.context = {"tenant": cls.installation.tenant, "project": cls.installation.project}
        cls.subject = "a" * 64
        cls.agent = Agent.objects.create(**cls.context, name="Personal assistant",
            created_by=cls.installation.owner, computer_requirement="required",
            workspace_capabilities=["files.read", "browser.control"])
        cls.computer = WorkspaceConnection.objects.create(**cls.context, name="Runtime computer",
            connection_type="runtime", owner_subject_type="user", owner_subject_hash=cls.subject,
            created_by=cls.installation.owner, workspace_root="/test-workspace")
        cls.binding = AgentComputerBinding.objects.create(**cls.context, agent=cls.agent,
            connection=cls.computer, caller_subject_hash=cls.subject, caller_principal_type="user")
        cls.display_run = AgentDisplayRun.objects.create(tenant=cls.installation.tenant, agent=cls.agent,
            computer_binding=cls.binding, consumer_tenant=cls.installation.tenant,
            consumer_project=cls.installation.project, caller_subject_hash=cls.subject,
            caller_principal_type="user", caller_principal_id=str(cls.installation.owner_id),
            run_kind="invocation", write_token="unit-test-only-not-a-live-credential",
            workspace_root=cls.computer.workspace_root)

    def test_fresh_graph_matches_real_models_without_private_tables(self):
        loader = MigrationLoader(connection)
        loader.graph.validate_consistency()
        self.assertFalse(loader.detect_conflicts())
        self.assertEqual(MigrationAutodetector(loader.project_state(),
            ProjectState.from_apps(apps)).changes(graph=loader.graph), {})
        names = set(connection.introspection.table_names())
        for label, count in (("agents", 39), ("workspaces", 7), ("mobile", 2)):
            models = list(apps.get_app_config(label).get_models())
            self.assertEqual(len(models), count)
            self.assertTrue({model._meta.db_table for model in models} <= names)
            for model in models:
                # Query every table, including tables not populated by these fixtures.
                model.objects.count()
        self.assertFalse(any(name.startswith(("billing_", "iam_", "tokenbank_",
            "marketplace_", "api_keys_")) for name in names))
        self.assertNotIn("agents_agentpricing", names)
        self.assertIn("workspaces_workspacetoolmanagedprofile", names)
        # The explicit Tool Setup schema must not install commercial key fields.
        from apps.workspaces import models
        profile_model = getattr(models, "WorkspaceToolManagedProfile")
        self.assertEqual(profile_model._meta.get_field("router_credential").related_model._meta.label,
                         "personal.PersonalRouterCredential")
        self.assertFalse({"api_key", "agent_api_key"} & {field.name for field in profile_model._meta.fields})
        with connection.cursor() as cursor:
            columns = connection.introspection.get_table_description(cursor, "agents_agentruntimeinvocation")
        self.assertFalse({"api_key_id", "cost", "billing_status", "reported_cost"} & {c.name for c in columns})

    def test_messages_events_and_turn_constraints_survive_round_trip(self):
        for index in (1, 2):
            AgentRunMessage.objects.create(run=self.display_run, sequence=index, turn_index=index,
                role="user", content=f"第 {index} 轮", source_message_id=f"message-{index}")
            AgentDisplayEvent.objects.create(tenant=self.installation.tenant, agent=self.agent,
                run=self.display_run, seq=index, event_type="TEXT_MESSAGE_CONTENT", payload_json={"turn": index})
            AgentRuntimeInvocation.objects.create(**self.context, agent=self.agent,
                display_run=self.display_run, actor=self.installation.owner, status="pending", turn_index=index)
        self.assertEqual(list(self.display_run.messages.order_by("sequence").values_list("content", flat=True)),
            ["第 1 轮", "第 2 轮"])
        self.assertEqual(self.display_run.runtime_invocations.count(), 2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AgentRunMessage.objects.create(run=self.display_run, sequence=3, role="user",
                content="Duplicate", source_message_id="message-1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            AgentRuntimeInvocation.objects.create(**self.context, agent=self.agent,
                display_run=self.display_run, status="pending", turn_index=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AgentDisplayEvent.objects.create(tenant=self.installation.tenant, agent=self.agent,
                run=self.display_run, seq=1, event_type="CUSTOM")

    def test_terminal_browser_and_attachment_lineage_are_persisted(self):
        terminal = WorkspaceTerminalSession.objects.create(**self.context, connection=self.computer,
            display_run=self.display_run, computer_binding=self.binding, session_kind="agent_run",
            caller_subject_hash=self.subject, authorized_root=self.computer.workspace_root)
        WorkspaceTerminalTranscript.objects.create(session=terminal, seq=1, kind="stdout", data="你好\r\n")
        browser = AgentBrowserSession.objects.create(run=self.display_run, connection=self.computer,
            platform="linux", runtime_session_id="schema-test-session")
        AgentRunComputerAttachment.objects.create(run=self.display_run, revision=1, binding=self.binding,
            terminal_session=terminal, browser_session=browser, computer_name=self.computer.name)
        restored = AgentRunComputerAttachment.objects.select_related("terminal_session", "browser_session").get(run=self.display_run)
        self.assertEqual(restored.terminal_session.transcript.get().data, "你好\r\n")
        self.assertEqual(restored.browser_session.platform, "linux")
        with self.assertRaises(IntegrityError), transaction.atomic():
            WorkspaceTerminalTranscript.objects.create(session=terminal, seq=1, kind="stdout", data="duplicate")
        with self.assertRaises(IntegrityError), transaction.atomic():
            AgentBrowserSession.objects.create(run=self.display_run, connection=self.computer)

    def test_command_deduplication_is_scoped_to_real_device(self):
        deadline = timezone.now() + timedelta(minutes=1)
        device = ComputerRuntimeDevice.objects.create(connection=self.computer,
            public_key_pem="schema-fixture-not-a-live-key", public_key_fingerprint="b" * 64,
            platform="linux", capabilities={"workspace.v1": True})
        values = {"device": device, "connection": self.computer, "display_run_id": self.display_run.id,
            "caller_subject_hash": self.subject, "required_scope": "files.read",
            "operation": "workspace.read", "idempotency_key": "read-1",
            "encrypted_payload": "schema-fixture-no-sensitive-payload", "expires_at": deadline}
        command = ComputerRuntimeCommand.objects.create(**values)
        self.assertEqual(ComputerRuntimeCommand.objects.get(pk=command.pk).device.connection_id, self.computer.pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ComputerRuntimeCommand.objects.create(**values)
        other_computer = WorkspaceConnection.objects.create(**self.context, name="Second", connection_type="runtime")
        other_device = ComputerRuntimeDevice.objects.create(connection=other_computer,
            public_key_fingerprint="c" * 64, platform="windows")
        other = ComputerRuntimeCommand.objects.create(**{**values, "device": other_device, "connection": other_computer})
        self.assertNotEqual(command.device_id, other.device_id)

    def test_mobile_binding_command_and_snapshot_round_trip(self):
        device = MobileDevice.objects.create(**self.context, name="Personal phone", token_hash="d" * 64,
            created_by=self.installation.owner, owner_subject_hash=self.subject)
        binding = AgentMobileBinding.objects.create(**self.context, agent=self.agent,
            device=device, caller_subject_hash=self.subject, caller_principal_type="user")
        self.display_run.mobile_binding = binding
        self.display_run.save(update_fields=["mobile_binding"])
        values = {**self.context, "device": device, "display_run": self.display_run, "mobile_binding": binding,
            "caller_subject_hash": self.subject, "action": "observe", "client_request_id": uuid.uuid4(),
            "screenshot": b"fixture-bytes", "screenshot_content_type": "image/png"}
        command = MobileCommand.objects.create(**values)
        self.assertEqual(bytes(MobileCommand.objects.get(pk=command.pk).screenshot), b"fixture-bytes")
        self.assertEqual(AgentDisplayRun.objects.get(pk=self.display_run.pk).mobile_binding.device_id, device.pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MobileCommand.objects.create(**values)

    def test_output_revision_and_task_lifetime_relationships(self):
        task = AgentExecutionTask.objects.create(run=self.display_run, agent=self.agent,
            caller_subject_hash=self.subject, tool_name="chat", expires_at=timezone.now() + timedelta(minutes=5))
        for revision in (1, 2):
            AgentOutputArtifact.objects.create(agent=self.agent, run=self.display_run,
                computer_revision=revision, workspace_path="result.json", original_file_name="result.json")
        self.assertEqual(self.display_run.output_artifacts.count(), 2)
        self.assertEqual(self.display_run.output_artifacts.first().tenant_id, self.installation.tenant_id)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AgentOutputArtifact.objects.create(agent=self.agent, run=self.display_run,
                computer_revision=1, workspace_path="result.json", original_file_name="result.json")
        invocation = AgentRuntimeInvocation.objects.create(**self.context, agent=self.agent,
            display_run=self.display_run, status="pending")
        # Real ORM cascade/null behavior, not process cleanup or runtime execution.
        self.display_run.delete()
        self.assertFalse(AgentExecutionTask.objects.filter(pk=task.pk).exists())
        self.assertFalse(AgentOutputArtifact.objects.exists())
        invocation.refresh_from_db()
        self.assertIsNone(invocation.display_run_id)
        self.assertTrue(WorkspaceConnection.objects.filter(pk=self.computer.pk).exists())

    def test_soft_deleted_default_binding_can_be_replaced_without_erasing_history(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            AgentComputerBinding.objects.create(**self.context, agent=self.agent, connection=self.computer,
                caller_subject_hash=self.subject, caller_principal_type="user")
        self.binding.delete()
        replacement = AgentComputerBinding.objects.create(**self.context, agent=self.agent,
            connection=self.computer, caller_subject_hash=self.subject, caller_principal_type="user")
        self.assertNotEqual(replacement.pk, self.binding.pk)
        self.display_run.refresh_from_db()
        self.assertEqual(self.display_run.computer_binding_id, self.binding.pk)
        self.assertEqual(self.display_run.computer_binding.status, "deleted")
