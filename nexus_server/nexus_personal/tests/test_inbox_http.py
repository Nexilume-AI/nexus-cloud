"""Real Inbox signals, queries, receipts and caller HTTP on personal schema."""
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.agents import runtime_services
from apps.notifications.models import InboxItem, InboxReceipt, UserNotification, WebPushSubscription, PushDelivery
from apps.notifications.inbox import upsert_item
from . import test_agent_file_http as file_http


class PersonalInboxHTTPTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        file_http.PersonalAgentFileHTTPTests.setUpTestData.__func__(cls)

    def setUp(self):
        file_http.PersonalAgentFileHTTPTests.setUp(self)
        self.headers = {"HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN": self.display_token()}

    display_token = file_http.PersonalAgentFileHTTPTests.display_token

    def finish(self):
        runtime_services.finish_invocation_display_run(run=self.run, succeeded=True)
        self.run.refresh_from_db()
        return InboxItem.objects.get(source_type="run", source_id=str(self.run.pk), kind="run_completed")

    def listing(self, suffix=""):
        response = self.client.get("/api/v1/inbox/items/" + suffix)
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        return response.data

    def test_completion_signal_creates_real_notice_and_display_read_synchronizes_receipt(self):
        item = self.finish()
        self.assertEqual(UserNotification.objects.filter(run=self.run, kind="run_completed").count(), 1)
        self.assertFalse(InboxReceipt.objects.exists())
        self.assertEqual([row["id"] for row in self.listing("?state=completed_unread")["results"]], [str(item.pk)])
        updated_at = self.run.updated_at
        response = self.client.post(self.base + "read/", {"completed_at": self.run.completed_at.isoformat()},
            format="json", **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        receipt = InboxReceipt.objects.get(item=item, user=self.row.owner)
        self.assertEqual(receipt.read_at, self.run.completed_at)
        self.run.refresh_from_db()
        self.assertEqual(self.run.caller_read_completed_at, receipt.read_at)
        self.assertEqual(self.run.updated_at, updated_at)
        self.assertEqual(self.listing("?state=completed_unread")["results"], [])
        self.assertFalse(self.client.get(self.base + "display/", **self.headers).data["completion_unread"])

    def test_question_live_state_is_queried_without_mutating_inbox_or_creating_receipts(self):
        self.run.interaction_mode = "task"
        self.run.save(update_fields=["interaction_mode"])
        question = runtime_services.create_run_interaction(run_id=str(self.run.pk), token=self.context.interaction_token,
            data={"key": "ask", "prompt": "Private prompt", "kind": "text"})
        item = InboxItem.objects.get(source_id=str(self.run.pk), kind="input_required")
        rows = self.listing("?state=needs_action")["results"]
        self.assertEqual([row["id"] for row in rows], [str(item.pk)])
        self.assertNotIn("Private prompt", str(rows))
        self.assertEqual(self.client.get("/api/v1/inbox/summary/").data["needs_attention"], 1)
        response = self.client.post(self.base + f"interactions/{question['id']}/reply/", {"text": "Private answer"},
            format="json", **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.listing("?state=needs_action")["results"], [])
        detail = self.client.get(f"/api/v1/inbox/items/{item.pk}/")
        self.assertEqual(detail.data["state"], "resolved")
        item.refresh_from_db()
        self.assertEqual(item.state, "needs_action")
        self.assertFalse(InboxReceipt.objects.exists())

    def test_source_replay_does_not_duplicate_notification_and_old_completion_is_rejected(self):
        from apps.notifications.sources import record_run
        item = self.finish()
        record_run(self.run)
        self.assertEqual(InboxItem.objects.filter(pk=item.pk).count(), 1)
        self.assertEqual(UserNotification.objects.filter(run=self.run).count(), 1)
        response = self.client.post(self.base + "read/",
            {"completed_at": (self.run.completed_at - timedelta(seconds=1)).isoformat()}, format="json", **self.headers)
        self.assertEqual(response.status_code, 409)
        self.assertFalse(InboxReceipt.objects.exists())

    def test_uuid_text_case_and_invalid_source_ids_keep_live_state_lookup_safe(self):
        from apps.notifications.querying import inbox_query
        from django.test import RequestFactory
        self.run.status = "running"
        self.run.save(update_fields=["status"])
        rows = []
        for index, source_id in enumerate((str(self.run.pk), str(self.run.pk).upper(), "not-a-uuid")):
            rows.append(upsert_item(tenant_id=self.row.tenant_id, project_id=self.row.project_id,
                recipient_id=self.row.owner_id, category="agent", kind="run_failed", state="needs_action",
                priority=50, source_type="run", source_id=source_id, event_key=f"uuid:{index}", navigation_key="agent_run"))
        request = RequestFactory().get("/api/v1/inbox/items/")
        request.user = self.row.owner
        states = dict(inbox_query(request).filter(pk__in=[r.pk for r in rows]).values_list("pk", "_live_state"))
        # A recovered Run resolves old failure notices for either UUID spelling.
        self.assertEqual([states[r.pk] for r in rows], ["resolved"] * 3)
        from django.db.models import UUIDField
        from apps.notifications.querying import SourceUUIDCast
        parsed = dict(InboxItem.objects.filter(pk__in=[r.pk for r in rows[:2]])
                      .annotate(parsed=SourceUUIDCast("source_id", UUIDField())).values_list("pk", "parsed"))
        self.assertEqual(list(parsed.values()), [self.run.pk, self.run.pk])

    def test_other_recipient_role_and_unavailable_product_items_are_not_visible_or_mutable(self):
        other = get_user_model().objects.create_user(username="inbox-other", email="inbox-other@example.test")
        for index, changes in enumerate((
            {"recipient_id": other.pk},
            {"audience_type": "role", "required_permission": "admin", "recipient_id": None},
            {"source_type": "unavailable_product"},
        )):
            item = upsert_item(**{**dict(tenant_id=self.row.tenant_id, project_id=self.row.project_id,
                recipient_id=self.row.owner_id, category="agent", kind="run_completed", state="completed",
                priority=50, source_type="run", source_id=self.run.pk, event_key=f"hidden:{index}", navigation_key="agent_run"), **changes})
            self.assertNotIn(str(item.pk), [row["id"] for row in self.listing()["results"]])
            for suffix in ("", "read/", "open/", "archive/"):
                path = f"/api/v1/inbox/items/{item.pk}/" + suffix
                response = self.client.get(path) if not suffix else self.client.post(path, {}, format="json")
                self.assertEqual(response.status_code, 404)
        self.assertFalse(InboxReceipt.objects.exists())
        self.assertEqual(APIClient().get("/api/v1/inbox/items/").status_code, 401)

    def test_signal_queues_only_owner_push_delivery_and_repeat_is_idempotent(self):
        from apps.notifications.sources import record_run
        subscription = WebPushSubscription.objects.create(user=self.row.owner, endpoint_hash="a" * 64,
            endpoint_encrypted="fixture", p256dh_encrypted="fixture", auth_encrypted="fixture", last_seen_at=timezone.now())
        item = self.finish()
        record_run(self.run)
        self.assertEqual(PushDelivery.objects.filter(item=item, subscription=subscription).count(), 1)
        self.assertEqual(PushDelivery.objects.get(item=item).status, "pending")
        # A queue row is evidence of scheduling only, not Web Push delivery.

    def test_inbox_open_and_explicit_receipt_actions_use_existing_run_destination(self):
        item = self.finish()
        response = self.client.post(f"/api/v1/inbox/items/{item.pk}/open/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["url"], f"/agents/{self.agent.pk}/private-display?run={self.run.pk}")
        self.assertEqual(InboxReceipt.objects.filter(item=item, user=self.row.owner).count(), 1)
        response = self.client.post(f"/api/v1/inbox/items/{item.pk}/archive/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.listing()["results"], [])
