"""Shared scale and worker-state assertions; not real push-network acceptance."""
import asyncio
import time
from datetime import timedelta
from unittest import mock

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from apps.jobs.models import Job
from apps.notifications.models import InboxItem, InboxReceipt, InboxWorkerCursor, PushDelivery, WebPushSubscription


class WorkInboxScaleGuards:
    def seed(self, count, *, state="completed"):
        now = timezone.now()
        jobs = Job.objects.bulk_create([Job(tenant=self.tenant, project=self.project, created_by=self.user,
            job_type="audit.scale", resource_type="dataset", resource_id="audit", status="succeeded" if state == "completed" else "running",
            completed_at=now if state == "completed" else None) for _ in range(count)])
        return InboxItem.objects.bulk_create([InboxItem(tenant=self.tenant, project=self.project, recipient=self.user,
            category="background", kind="job_succeeded", state=state, audience_key=f"user:{self.user.pk}",
            source_type="job", source_id=str(job.pk), event_key=f"job:{job.pk}", navigation_key="job",
            title_key="job_succeeded", occurred_at=now, resolved_at=now if state == "completed" else None) for job in jobs])

    def test_thousand_items_use_constant_queries_and_read_only_gets(self):
        counts = {}
        for total, added in [(100, 100), (1000, 900), (10000, 9000)]:
            self.seed(added)
            self.get("summary/")  # Warm existing authorization bootstrap.
            for endpoint in ["summary/", "items/"]:
                start = time.perf_counter()
                with CaptureQueriesContext(connection) as queries:
                    response = self.get(endpoint)
                self.assertEqual(response.status_code, 200, response.content)
                data = self.inbox_payload(response)
                self.assertEqual(data["total"] if endpoint == "summary/" else len(data["results"]), total if endpoint == "summary/" else 30)
                self.assertFalse(any(q["sql"].lstrip().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))
                counts[total, endpoint] = len(queries)
                print(f"INBOX_SCALE items={total} endpoint={endpoint} queries={len(queries)} seconds={time.perf_counter()-start:.3f}", flush=True)
        for endpoint in ["summary/", "items/"]:
            self.assertLessEqual(counts[1000, endpoint], counts[100, endpoint] + 2)
            self.assertLess(counts[1000, endpoint], 60)
            self.assertLessEqual(counts[10000, endpoint], counts[100, endpoint] + 2)

    @override_settings(NEXUS_INBOX_RECONCILE_BATCH_SIZE=2)
    def test_daily_repair_finds_changes_outside_incremental_overlap(self):
        from apps.notifications.tasks import _batch
        self.seed(1)
        stale = timezone.now() - timedelta(days=2)
        Job.objects.filter(tenant=self.tenant).update(updated_at=stale)
        InboxWorkerCursor.objects.create(name=self.inbox_daily_batch_name, position={
            "since": timezone.now().isoformat(), "full_at": stale.isoformat(),
        })
        self.assertEqual(len(list(_batch(self.inbox_daily_batch_name, Job.objects.filter(tenant=self.tenant)))), 1)

    def test_live_source_state_is_counted_without_get_writes(self):
        item = self.seed(1, state="in_progress")[0]
        Job.objects.filter(pk=item.source_id).update(status="succeeded", completed_at=timezone.now())
        self.assertEqual(self.inbox_payload(self.get("summary/"))["completed_unread"], 1)
        self.assertEqual(len(self.inbox_payload(self.get("items/", {"state": "completed_unread"}))["results"]), 1)
        item.refresh_from_db()
        self.assertEqual(item.state, "in_progress")

    def test_unchanged_projection_does_not_write_or_change_revision(self):
        from apps.notifications.sources import record_job
        item = self.seed(1)[0]
        job = Job.objects.get(pk=item.source_id)
        record_job(job)
        item.refresh_from_db()
        before = item.updated_at
        with CaptureQueriesContext(connection) as queries:
            record_job(job)
        item.refresh_from_db()
        self.assertEqual(item.updated_at, before)
        self.assertFalse(any(q["sql"].startswith('UPDATE "notifications_inboxitem"') for q in queries))

    @override_settings(NEXUS_INBOX_RECONCILE_BATCH_SIZE=2)
    def test_reconcile_batches_advance_and_retry_without_skipping(self):
        from apps.notifications.tasks import _batch
        self.seed(5)
        qs = Job.objects.filter(tenant=self.tenant)
        first = list(_batch(self.inbox_retry_batch_name, qs))
        second = list(_batch(self.inbox_retry_batch_name, qs))
        third = list(_batch(self.inbox_retry_batch_name, qs))
        self.assertEqual([len(first), len(second), len(third)], [2, 2, 1])
        self.assertEqual(len({job.pk for job in first + second + third}), 5)
        cursor = InboxWorkerCursor.objects.get(name=self.inbox_retry_batch_name)
        saved = cursor.position
        pending = _batch(self.inbox_retry_batch_name, qs)
        next(pending)
        pending.close()
        cursor.refresh_from_db()
        self.assertEqual(cursor.position, saved)

    def delivery(self):
        item = self.seed(1)[0]
        subscription = WebPushSubscription.objects.create(user=self.user, endpoint_hash="scale-test",
            endpoint_encrypted="encrypted", p256dh_encrypted="encrypted", auth_encrypted="encrypted", last_seen_at=timezone.now())
        return PushDelivery.objects.create(item=item, subscription=subscription)

    def test_push_claim_recovery_and_exhaustion(self):
        from apps.notifications.push import claim_deliveries, _send_claimed
        delivery = self.delivery()
        first = claim_deliveries(1)
        self.assertEqual(len(first), 1)
        self.assertEqual(claim_deliveries(1), [])
        PushDelivery.objects.filter(pk=delivery.pk).update(lease_expires_at=timezone.now()-timedelta(seconds=1))
        second = claim_deliveries(1)
        self.assertNotEqual(first[0][1], second[0][1])
        with mock.patch("apps.notifications.push._deliver") as send:
            self.assertEqual(_send_claimed(first[0]), "skipped")
            send.assert_not_called()
        PushDelivery.objects.filter(pk=delivery.pk).update(attempts=8, lease_expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(claim_deliveries(1), [])
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, "exhausted")

    def test_inflight_old_push_cannot_overwrite_new_source_event(self):
        from apps.notifications.push import claim_deliveries, _send_claimed
        from apps.notifications.inbox import queue_push_deliveries
        delivery = self.delivery()
        claim = claim_deliveries(1)[0]
        with mock.patch("apps.notifications.push._deliver", side_effect=lambda *_: queue_push_deliveries(delivery.item, reset=True)):
            with mock.patch("apps.notifications.push._allowed", return_value=True):
                _send_claimed(claim)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, "pending")
        self.assertIsNone(delivery.lease_token)


class InboxStreamScaleGuards:
    def test_first_asgi_event_is_immediate_and_does_not_compute_summary(self):
        from apps.notifications.inbox_views import InboxStreamView
        request = mock.Mock()
        with mock.patch("apps.notifications.inbox_views.require_personal_user"), \
             mock.patch("apps.notifications.inbox_views.get_tenant_from_request"), \
             mock.patch("apps.notifications.inbox_views._revision", return_value="revision"), \
             mock.patch("apps.notifications.inbox_views._summary", side_effect=AssertionError("Full scan")):
            response = InboxStreamView().get(request)
            self.assertTrue(response.is_async)
            async def receive():
                iterator = response.streaming_content
                first = await asyncio.wait_for(anext(iterator), timeout=0.2)
                self.assertIn(b"event: ready", first)
                await iterator.aclose()
            asyncio.run(receive())


class InboxConcurrentPushGuards:
    def test_two_workers_claim_distinct_deliveries_and_send_in_parallel(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.contrib.auth import get_user_model
        from django.db import close_old_connections
        from apps.tenancy.models import Tenant
        from apps.notifications.push import claim_deliveries, _thread_send

        recipients = self.concurrent_push_fixture()
        barrier = Barrier(2)
        def claim(_):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return claim_deliveries(1)[0]
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as workers:
            claims = list(workers.map(claim, range(2)))
        self.assertEqual(len({pk for pk, _ in claims}), 2)
        send_barrier = Barrier(2)
        def send(subscription, payload):
            self.assertEqual(recipients[payload["item_id"]], subscription.pk)
            send_barrier.wait(timeout=10)
        with mock.patch("apps.notifications.push._deliver", side_effect=send) as deliver:
            with ThreadPoolExecutor(max_workers=2) as workers:
                outcomes = list(workers.map(_thread_send, claims))
        self.assertEqual(outcomes, ["sent", "sent"])
        self.assertEqual(deliver.call_count, 2)
        self.assertEqual(PushDelivery.objects.filter(status="sent").count(), 2)
