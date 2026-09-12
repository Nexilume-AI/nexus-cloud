from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.agents.models import Agent, AgentDisplayRun, AgentMemoryItem
from apps.common.models import SoftDeleteModel
from apps.tenancy.models import Project, Tenant


class AgentInvocationMemoryMutationTests(TestCase):
    def setUp(self) -> None:
        self.developer = get_user_model().objects.create_user(
            username="memory-developer",
            email="memory-developer@example.com",
            password="password",
        )
        self.producer = Tenant.objects.create(name="Memory Producer", slug="memory-producer")
        self.consumer = Tenant.objects.create(name="Memory Consumer", slug="memory-consumer")
        self.project = Project.objects.create(tenant=self.consumer, name="Memory Project")
        self.other_project = Project.objects.create(tenant=self.consumer, name="Other Memory Project")
        self.agent = Agent.objects.create(
            tenant=self.producer,
            name="Memory Agent",
            visibility=Agent.VISIBILITY_PUBLIC,
            created_by=self.developer,
        )
        self.other_agent = Agent.objects.create(
            tenant=self.producer,
            name="Other Memory Agent",
            visibility=Agent.VISIBILITY_PUBLIC,
            created_by=self.developer,
        )
        self.client = APIClient()

    def _run(
        self,
        *,
        caller: str = "caller-a",
        project: Project | None = None,
        token: str = "memory-token",
        status: str = AgentDisplayRun.STATUS_RUNNING,
    ) -> AgentDisplayRun:
        return AgentDisplayRun.objects.create(
            tenant=self.producer,
            consumer_tenant=self.consumer,
            consumer_project=project or self.project,
            agent=self.agent,
            run_kind=AgentDisplayRun.KIND_INVOCATION,
            caller_subject_hash=caller,
            caller_principal_type="user",
            caller_principal_id=caller,
            write_token=token,
            status=status,
        )

    def _memory(
        self,
        *,
        source_run: AgentDisplayRun,
        scope: str = AgentMemoryItem.SCOPE_CALLER,
        caller: str = "caller-a",
        project: Project | None = None,
        agent: Agent | None = None,
        text: str = "Original private preference",
    ) -> AgentMemoryItem:
        return AgentMemoryItem.objects.create(
            tenant=self.consumer,
            project=project or self.project,
            agent=agent or self.agent,
            memory_type=AgentMemoryItem.TYPE_PREFERENCE,
            scope=scope,
            caller_subject_hash=caller if scope == AgentMemoryItem.SCOPE_CALLER else "",
            content_text=text,
            content_json={"format": "markdown"},
            source_run=source_run,
            confidence="0.9000",
            consent_status=AgentMemoryItem.CONSENT_APPROVED,
            license_status=AgentMemoryItem.LICENSE_INTERNAL,
        )

    def _detail(self, run: AgentDisplayRun, item: AgentMemoryItem) -> str:
        return f"/api/v1/internal/agent-runs/{run.id}/memory/{item.id}/"

    def test_update_and_delete_preserve_lineage_and_write_private_metadata_only_events(self) -> None:
        historical_run = self._run(token="historical", status=AgentDisplayRun.STATUS_COMPLETED)
        item = self._memory(source_run=historical_run)
        run = self._run(token="current-token")

        updated = self.client.patch(
            self._detail(run, item),
            {
                "expected_revision": 1,
                "content_text": "Prefer concise Markdown reports",
                "content_json": {},
                "confidence": "0.9800",
            },
            format="json",
            HTTP_X_NEXUS_WORKSPACE_TOKEN="current-token",
        )

        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(updated.json()["data"]["revision"], 2)
        self.assertEqual(updated.json()["data"]["text"], "Prefer concise Markdown reports")
        item.refresh_from_db()
        self.assertEqual(item.source_run, historical_run)
        self.assertEqual(item.revision, 2)
        update_event = run.events.get(payload_json__name="nexus.memory.item.updated")
        self.assertEqual(update_event.visibility, "private")
        self.assertEqual(update_event.payload_json["value"]["changed_fields"], ["confidence", "data", "text"])
        self.assertNotIn("Prefer concise", json.dumps(update_event.payload_json))
        self.assertIn(str(update_event.id), item.source_event_ids)

        deleted = self.client.delete(
            self._detail(run, item),
            {"expected_revision": 2},
            format="json",
            HTTP_X_NEXUS_WORKSPACE_TOKEN="current-token",
        )

        self.assertEqual(deleted.status_code, 200, deleted.content)
        self.assertEqual(deleted.json()["data"], {"id": str(item.id), "deleted": True, "revision": 3})
        item.refresh_from_db()
        self.assertEqual(item.status, SoftDeleteModel.STATUS_DELETED)
        self.assertEqual(item.revision, 3)
        delete_event = run.events.get(payload_json__name="nexus.memory.item.deleted")
        self.assertEqual(delete_event.visibility, "private")
        self.assertNotIn("Prefer concise", json.dumps(delete_event.payload_json))
        self.assertIn(str(delete_event.id), item.source_event_ids)
        recalled = self.client.get(
            f"/api/v1/internal/agent-runs/{run.id}/memory/",
            HTTP_X_NEXUS_WORKSPACE_TOKEN="current-token",
        )
        self.assertEqual(recalled.status_code, 200, recalled.content)
        self.assertNotIn(str(item.id), {value["id"] for value in recalled.json()["data"]["items"]})

    def test_recall_and_mutation_are_scoped_to_caller_tenant_project_and_agent(self) -> None:
        run = self._run(token="scope-token")
        own = self._memory(source_run=run)
        other_caller = self._memory(source_run=run, caller="caller-b", text="Other caller")
        other_project = self._memory(source_run=run, project=self.other_project, text="Other project")
        other_agent = self._memory(source_run=run, agent=self.other_agent, text="Other agent")
        developer_only = self._memory(
            source_run=run,
            scope=AgentMemoryItem.SCOPE_DEVELOPER_ONLY,
            text="Developer only",
        )
        global_item = self._memory(
            source_run=run,
            scope=AgentMemoryItem.SCOPE_AGENT_GLOBAL,
            text="Approved global",
        )

        recalled = self.client.get(
            f"/api/v1/internal/agent-runs/{run.id}/memory/",
            HTTP_X_NEXUS_WORKSPACE_TOKEN="scope-token",
        )
        self.assertEqual(recalled.status_code, 200, recalled.content)
        recalled_ids = {value["id"] for value in recalled.json()["data"]["items"]}
        self.assertEqual(recalled_ids, {str(own.id), str(global_item.id)})
        own_payload = next(value for value in recalled.json()["data"]["items"] if value["id"] == str(own.id))
        self.assertEqual(own_payload["revision"], 1)
        self.assertIn("updated_at", own_payload)

        for protected in (other_caller, other_project, other_agent, developer_only, global_item):
            response = self.client.patch(
                self._detail(run, protected),
                {"expected_revision": 1, "content_text": "unauthorized change"},
                format="json",
                HTTP_X_NEXUS_WORKSPACE_TOKEN="scope-token",
            )
            self.assertEqual(response.status_code, 404, response.content)
            protected.refresh_from_db()
            self.assertNotEqual(protected.content_text, "unauthorized change")

    def test_two_runs_with_same_recalled_revision_do_not_silently_overwrite(self) -> None:
        first_run = self._run(token="first-token")
        second_run = self._run(token="second-token")
        item = self._memory(source_run=first_run)

        first = self.client.patch(
            self._detail(first_run, item),
            {"expected_revision": 1, "content_text": "First update"},
            format="json",
            HTTP_X_NEXUS_WORKSPACE_TOKEN="first-token",
        )
        stale = self.client.patch(
            self._detail(second_run, item),
            {"expected_revision": 1, "content_text": "Stale update"},
            format="json",
            HTTP_X_NEXUS_WORKSPACE_TOKEN="second-token",
        )

        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(stale.status_code, 409, stale.content)
        self.assertEqual(stale.json()["error"]["code"], "MEMORY_REVISION_CONFLICT")
        self.assertEqual(stale.json()["error"]["current_revision"], 2)
        item.refresh_from_db()
        self.assertEqual(item.content_text, "First update")
        self.assertEqual(item.revision, 2)
        self.assertFalse(second_run.events.filter(payload_json__name="nexus.memory.item.updated").exists())

    def test_closed_run_and_deleted_memory_fail_closed(self) -> None:
        run = self._run(token="closed-token")
        item = self._memory(source_run=run)
        run.status = AgentDisplayRun.STATUS_COMPLETED
        run.save(update_fields=["status", "updated_at"])

        closed = self.client.patch(
            self._detail(run, item),
            {"expected_revision": 1, "content_text": "closed update"},
            format="json",
            HTTP_X_NEXUS_WORKSPACE_TOKEN="closed-token",
        )
        self.assertEqual(closed.status_code, 409, closed.content)
        self.assertEqual(closed.json()["error"]["code"], "AGENT_RUN_CLOSED")

        active_run = self._run(token="active-token")
        item.delete()
        deleted = self.client.delete(
            self._detail(active_run, item),
            {"expected_revision": 1},
            format="json",
            HTTP_X_NEXUS_WORKSPACE_TOKEN="active-token",
        )
        self.assertEqual(deleted.status_code, 404, deleted.content)
