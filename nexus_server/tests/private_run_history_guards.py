"""Portable history/read-receipt guards; real HTTP/state, not live Agent execution."""
from datetime import timedelta
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from apps.agents.models import AgentDisplayRun, AgentRunInteraction


class PrivateRunHistoryGuards:
    def complete(self):
        self.run.status = "completed"
        self.run.completed_at = timezone.now()
        self.run.save(update_fields=["status", "completed_at"])

    def listing(self, **query):
        return self.client.get(self.path, query, **self._headers(self.project_a))

    def mark(self, **overrides):
        return self.client.post(self.read_url, {"completed_at": self.run.completed_at.isoformat(), **overrides}, format="json", **self.headers)

    def clone(self, **overrides):
        return AgentDisplayRun.objects.create(tenant=self.run.tenant, agent=self.run.agent,
            consumer_tenant=self.run.consumer_tenant, consumer_project=self.run.consumer_project,
            caller_subject_hash=self.run.caller_subject_hash, run_kind="invocation", write_token="test-only",
            **overrides)

    def test_waiting_running_completed_and_expired_questions(self):
        self.assertEqual(self.history_payload(self.listing())["counts"]["running"], 1)
        question = AgentRunInteraction.objects.create(run=self.run, key="q", prompt="Confirm?", expires_at=timezone.now() + timedelta(minutes=5))
        data = self.history_payload(self.listing(attention="input_required"))
        self.assertEqual(data["counts"]["input_required"], 1)
        self.assertEqual(data["counts"]["running"], 0)
        self.assertEqual(data["results"][0]["attention_state"], "input_required")
        question.expires_at = timezone.now() - timedelta(seconds=1)
        question.save(update_fields=["expires_at"])
        self.assertEqual(self.history_payload(self.listing())["counts"]["running"], 1)
        self.complete()
        data = self.history_payload(self.listing(attention="completed_unread"))
        self.assertEqual(data["counts"]["completed_unread"], 1)
        self.assertEqual(data["results"][0]["attention_state"], "completed_unread")

    def test_read_is_explicit_idempotent_and_next_completion_is_unread(self):
        self.complete()
        self.client.get(f"/api/v1/agent-runs/{self.run.pk}/display/", **self.headers)
        self.assertTrue(self.history_payload(self.listing())["results"][0]["completion_unread"])
        self.run.refresh_from_db()
        updated = self.run.updated_at
        self.assertEqual(self.mark().status_code, 200)
        self.assertEqual(self.mark().status_code, 200)
        self.run.refresh_from_db()
        self.assertEqual(self.run.updated_at, updated)
        self.assertEqual(self.history_payload(self.listing(attention="completed_unread"))["results"], [])
        previous = self.run.completed_at
        self.complete()
        self.assertEqual(self.mark(completed_at=previous.isoformat()).status_code, 409)
        self.assertEqual(self.history_payload(self.listing())["counts"]["completed_unread"], 1)

    def test_filter_counts_cover_full_history_and_cursor_survives_state_change(self):
        self.complete()
        for i in range(31):
            self.clone(status="completed", completed_at=timezone.now(), display_title=f"result {i}")
        page = self.history_payload(self.listing(attention="completed_unread", limit=30))
        self.assertEqual(len(page["results"]), 30)
        self.assertEqual(page["counts"]["completed_unread"], 32)
        anchor = AgentDisplayRun.objects.get(pk=page["next_cursor"])
        AgentDisplayRun.objects.filter(pk=anchor.pk).update(caller_read_completed_at=anchor.completed_at)
        tail = self.history_payload(self.listing(attention="completed_unread", cursor=page["next_cursor"]))
        self.assertEqual(len(tail["results"]), 2)
        self.assertEqual(tail["counts"]["completed_unread"], 31)
        self.assertEqual(self.history_payload(self.listing(q="result 30"))["counts"]["all"], 1)

    def test_bad_filter_timestamp_and_running_receipt_are_rejected(self):
        self.assertEqual(self.listing(attention="forged").status_code, 400)
        self.run.completed_at = timezone.now()
        self.assertEqual(self.mark().status_code, 409)
        self.assertEqual(self.mark(completed_at="invalid").status_code, 400)

    def test_rename_and_token_renewal_do_not_create_unread_completion(self):
        self.complete()
        self.assertEqual(self.mark().status_code, 200)
        self._display_headers(str(self.run.pk))
        AgentDisplayRun.objects.filter(pk=self.run.pk).update(display_title="Renamed", updated_at=timezone.now())
        self.assertFalse(self.history_payload(self.listing())["results"][0]["completion_unread"])

    def test_history_serialization_query_count_does_not_grow_per_run(self):
        with CaptureQueriesContext(connection) as one:
            self.assertEqual(self.listing().status_code, 200)
        for i in range(8):
            self.clone(status="running", display_title=f"task {i}")
        with CaptureQueriesContext(connection) as many:
            self.assertEqual(len(self.history_payload(self.listing())["results"]), 9)
        self.assertLessEqual(len(many), len(one) + 1)
