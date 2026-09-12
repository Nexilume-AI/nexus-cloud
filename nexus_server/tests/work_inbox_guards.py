"""Unchanged common Inbox assertions; network delivery remains explicitly simulated."""
from datetime import datetime as dt_datetime, time as dt_time, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from unittest import mock

from django.test import override_settings
from django.utils import timezone
from apps.agents.models import AgentRunInteraction
from apps.jobs.models import Job
from apps.notifications.models import InboxItem, InboxReceipt, PushDelivery, WebPushSubscription
from apps.workspaces.models import WorkspaceConnection


class WorkInboxGuards:
    def test_run_is_canonical_safe_item_and_read_is_only_a_receipt(self):
        run = self.new_run()
        summary = self.inbox_payload(self.get("summary/"))
        self.assertEqual(summary["completed_unread"], 1)
        self.assertEqual(summary["unread"], 1)
        self.assertEqual(summary["badge_count"], 0, "Historical results must not inflate the attention badge.")
        self.assertFalse(InboxReceipt.objects.exists(), "Read-only Inbox queries must not create receipts.")
        page = self.inbox_payload(self.get("items/"))
        self.assertFalse(InboxReceipt.objects.exists(), "Listing work must remain side-effect free.")
        item = page["results"][0]
        self.assertEqual(item["title"], "Run completed")
        self.assertNotIn("DO_NOT_RETURN", str(page))
        self.assertEqual(self.post(f"items/{item['id']}/read/").status_code, 200)
        run.refresh_from_db()
        self.assertEqual(run.status, "completed")
        self.assertTrue(InboxReceipt.objects.get(item_id=item["id"], user=self.user).read_at)

    def test_badge_count_matches_the_needs_attention_queue(self):
        self.new_run()
        waiting = self.new_run(status="running")
        AgentRunInteraction.objects.create(
            run=waiting,
            key="choose",
            prompt="PRIVATE QUESTION",
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        summary = self.inbox_payload(self.get("summary/"))
        attention = self.inbox_payload(self.get("items/", {"state": "needs_action"}))["results"]
        self.assertEqual(summary["unread"], 2)
        self.assertEqual(summary["badge_count"], len(attention))
        self.assertEqual(summary["badge_count"], summary["needs_attention"])

    def test_pending_interaction_can_be_snoozed_but_not_archived_or_answered(self):
        run = self.new_run(status="running")
        interaction = AgentRunInteraction.objects.create(run=run, key="confirm", prompt="PRIVATE QUESTION",
            expires_at=timezone.now() + timedelta(minutes=10))
        item = self.inbox_payload(self.get("items/", {"state": "needs_action"}))["results"][0]
        self.assertEqual(self.post(f"items/{item['id']}/archive/").status_code, 400)
        until = timezone.now() + timedelta(hours=2)
        self.assertEqual(self.post(f"items/{item['id']}/snooze/", {"until": until.isoformat()}).status_code, 200)
        interaction.refresh_from_db()
        self.assertEqual(interaction.status, "pending")
        payload = self.inbox_payload(self.get("items/", {"state": "snoozed"}))
        self.assertEqual(payload["results"][0]["state"], "snoozed")
        self.assertNotIn("PRIVATE QUESTION", str(payload))

    def test_all_user_jobs_share_one_item_and_never_expose_payload_or_error(self):
        job = Job.objects.create(tenant=self.tenant, project=self.project, created_by=self.user,
            job_type="datasets.custom-index", resource_type="dataset", resource_id="opaque",
            status="queued", input_json={"prompt": "SECRET"})
        self.assertEqual(InboxItem.objects.filter(source_type="job", source_id=str(job.pk)).count(), 1)
        job.status, job.started_at = "running", timezone.now()
        job.save(update_fields=["status", "started_at", "updated_at"])
        job.status, job.completed_at, job.error_message = "failed", timezone.now(), "PRIVATE FAILURE"
        job.save(update_fields=["status", "completed_at", "error_message", "updated_at"])
        item = InboxItem.objects.get(source_type="job", source_id=str(job.pk))
        self.assertEqual(item.state, "failed")
        payload = self.inbox_payload(self.get("items/", {"state": "failed"}))
        self.assertNotIn("SECRET", str(payload))
        self.assertNotIn("PRIVATE FAILURE", str(payload))

    @override_settings(NEXUS_WEB_PUSH_ENABLED=True, NEXUS_VAPID_PUBLIC_KEY="public", NEXUS_VAPID_PRIVATE_KEY="private", NEXUS_VAPID_SUBJECT="mailto:ops@example.test")
    def test_push_subscription_is_encrypted_and_endpoint_is_never_returned(self):
        endpoint = "https://push.example.test/private-endpoint"
        response = self.post("push-subscriptions/", {"endpoint": endpoint, "keys": {"p256dh": "public-key", "auth": "secret-auth"}, "device_name": "QA Browser"})
        self.assertEqual(response.status_code, 201, response.content)
        row = WebPushSubscription.objects.get()
        self.assertNotIn(endpoint, row.endpoint_encrypted)
        listing = self.inbox_payload(self.get("push-subscriptions/"))
        self.assertNotIn("endpoint", str(listing).lower())
        preference = self.inbox_payload(self.get("preferences/"))
        self.assertTrue(preference["push_enabled"])

    @override_settings(NEXUS_WEB_PUSH_ENABLED=True, NEXUS_VAPID_PUBLIC_KEY="public", NEXUS_VAPID_PRIVATE_KEY="private", NEXUS_VAPID_SUBJECT="mailto:ops@example.test")
    def test_push_payload_is_generic_and_source_transition_requeues_delivery(self):
        self.post("push-subscriptions/", {"endpoint": "https://push.example.test/device", "keys": {"p256dh": "key", "auth": "auth"}})
        job = Job.objects.create(tenant=self.tenant, project=self.project, created_by=self.user,
            job_type="datasets.private-task", resource_type="dataset", resource_id="secret-resource", status="queued",
            input_json={"prompt": "PRIVATE PROMPT"})
        delivery = PushDelivery.objects.get(item__source_id=str(job.pk))
        delivery.status = PushDelivery.STATUS_SKIPPED
        delivery.save(update_fields=["status", "updated_at"])
        job.status, job.completed_at = "succeeded", timezone.now()
        job.save(update_fields=["status", "completed_at", "updated_at"])
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, PushDelivery.STATUS_PENDING)
        with mock.patch("apps.notifications.push._deliver") as send:
            from apps.notifications.push import deliver_pending_pushes
            self.assertEqual(deliver_pending_pushes()["sent"], 1)
        payload = send.call_args.args[1]
        self.assertEqual(payload["body"], "Your background task completed")
        self.assertNotIn("PRIVATE PROMPT", str(payload))
        self.assertNotIn("secret-resource", str(payload))

    def test_actionable_operational_issue_resolves_in_place(self):
        connection = WorkspaceConnection.objects.create(
            tenant=self.tenant,
            project=self.project,
            name="Caller Computer",
            **self.work_inbox_connection_fields,
            created_by=self.user,
            last_test_status=WorkspaceConnection.TEST_FAILED,
        )
        from apps.notifications.sources import record_operational_issue
        item = record_operational_issue(
            source=connection,
            kind="computer_issue",
            active=True,
            navigation_key="computer",
            recipient_id=self.user.pk,
        )
        self.assertEqual(item.state, InboxItem.STATE_NEEDS_ACTION)
        connection.last_test_status = WorkspaceConnection.TEST_SUCCEEDED
        connection.save(update_fields=["last_test_status", "updated_at"])
        payload = self.inbox_payload(self.get("items/", {"state": "resolved"}))
        self.assertEqual(payload["results"][0]["id"], str(item.pk))
        self.assertEqual(payload["results"][0]["state"], InboxItem.STATE_RESOLVED)

    def test_quiet_hours_handle_cross_midnight_and_dst(self):
        from apps.notifications.push import _quiet
        preference = SimpleNamespace(
            dnd_enabled=True,
            dnd_start=dt_time(22, 0),
            dnd_end=dt_time(8, 0),
            timezone="America/New_York",
        )
        # 2026-11-01 06:30 UTC is 01:30 after the fall-back transition.
        self.assertTrue(_quiet(preference, dt_datetime(2026, 11, 1, 6, 30, tzinfo=dt_timezone.utc)))
        self.assertFalse(_quiet(preference, dt_datetime(2026, 11, 1, 18, 0, tzinfo=dt_timezone.utc)))

    @override_settings(NEXUS_WEB_PUSH_ENABLED=True, NEXUS_VAPID_PUBLIC_KEY="public", NEXUS_VAPID_PRIVATE_KEY="private", NEXUS_VAPID_SUBJECT="mailto:ops@example.test")
    def test_expired_push_endpoint_is_disabled_without_leaking_it(self):
        endpoint = "https://push.example.test/expired-private-endpoint"
        response = self.post("push-subscriptions/", {
            "endpoint": endpoint,
            "keys": {"p256dh": "key", "auth": "auth"},
        })
        self.assertEqual(response.status_code, 201, response.content)
        run = self.new_run(status="running")
        AgentRunInteraction.objects.create(
            run=run,
            key="approve",
            prompt="PRIVATE",
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        failure = RuntimeError("provider detail must not escape")
        failure.response = SimpleNamespace(status_code=410)
        with mock.patch("apps.notifications.push._deliver", side_effect=failure):
            from apps.notifications.push import deliver_pending_pushes
            report = deliver_pending_pushes()
        self.assertEqual(report["failed"], 1)
        subscription = WebPushSubscription.objects.get()
        self.assertFalse(subscription.enabled)
        self.assertNotIn(endpoint, str(PushDelivery.objects.get().error_code))


class WorkInboxPostgresGuards:
    def test_reconciler_repairs_a_missed_background_task_once(self):
        job = Job.objects.create(
            tenant=self.tenant,
            project=self.project,
            created_by=self.user,
            job_type="datasets.reconcile-me",
            resource_type="dataset",
            resource_id="opaque",
            status="running",
            started_at=timezone.now(),
        )
        InboxItem.objects.filter(source_type="job", source_id=str(job.pk)).delete()
        from apps.notifications.tasks import reconcile_work_inbox

        first = reconcile_work_inbox()
        second = reconcile_work_inbox()
        self.assertGreaterEqual(first["active_checked"], 1)
        self.assertEqual(InboxItem.objects.filter(source_type="job", source_id=str(job.pk)).count(), 1)
        self.assertGreaterEqual(second["active_checked"], 1)

