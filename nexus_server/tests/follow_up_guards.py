"""Portable queue/steer assertions; authority and HTTP envelopes belong to each host."""
import json
from datetime import timedelta
from unittest import mock
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import threading
import asyncio
from pathlib import Path
import sys
import time
from urllib.parse import urlparse

from django.test import TestCase, TransactionTestCase, override_settings
from django.db import connections
from rest_framework.test import APIClient
from django.utils import timezone

from apps.agents import follow_ups, task_execution as worker
from apps.agents.models import AgentDisplayRun, AgentExecutionTask, AgentRunFollowUp, AgentRuntimeInvocation
from apps.agents.runtime_runner import RuntimeMCPResult
from apps.common.crypto import decrypt_secret


class FollowUpHelpers:
    def send(self, mode="queue", content="next", key="input-1", target=1):
        return self.client.post(self.path, {"mode": mode, "content": content,
            "idempotency_key": key, "turn_index": target}, format="json", **self.headers)

    def inbox(self, action, **values):
        return self.client.post(self.internal, {"action": action, "turn_index": 1, **values},
            format="json", HTTP_X_NEXUS_INTERACTION_TOKEN=self.token)

    def finish(self, lease=None, fail=False, tool_error=False):
        lease = lease or worker.claim()
        body = (b'{"jsonrpc":"2.0","result":{"isError":true,"content":[{"type":"text","text":"Task failed"}]}}'
                if tool_error else b'{"jsonrpc":"2.0","result":{"content":[]}}')
        result = RuntimeMCPResult(status_code=200, headers={}, body=body)
        with mock.patch("apps.agents.runtime_services.initialize_private_runtime_mcp_session", return_value={}), \
            mock.patch("apps.agents.runtime_services.close_private_runtime_mcp_session"), \
            mock.patch("apps.agents.runtime_services.call_runtime_mcp", side_effect=RuntimeError("connection lost") if fail else None, return_value=result) as call:
            worker.execute(*lease)
        return call.call_count

class FollowUpStateGuards:
    def test_queue_is_durable_idempotent_and_does_not_dispatch_while_running(self):
        first = self.send()
        self.assertEqual(first.status_code, 202, first.content)
        self.assertEqual(self.follow_payload(self.send())["id"], self.follow_payload(first)["id"])
        self.assertEqual(self.send(content="different").status_code, 409)
        row = AgentRunFollowUp.objects.get()
        self.assertNotIn('"subject"', row.encrypted_authority)
        self.assertEqual(json.loads(decrypt_secret(row.encrypted_authority))["subject"]["principal_id"], str(self.caller_a.pk))
        self.assertNotIn(self.token, row.encrypted_authority)
        self.assertEqual(follow_ups.dispatch_pending(), 0)
        self.assertEqual(self.task.run.messages.count(), 1)

    def test_delivery_lookup_is_read_only_exact_and_keeps_list_compatibility(self):
        first = self.follow_payload(self.send())
        before = (AgentRunFollowUp.objects.count(), self.task.run.messages.count())
        found = self.client.get(self.path, {"idempotency_key": "input-1"}, **self.headers)
        self.assertEqual(found.status_code, 200)
        self.assertEqual(self.follow_payload(found)["submission"]["id"], first["id"])
        self.assertNotIn("encrypted_authority", self.follow_payload(found)["submission"])
        missing = self.client.get(self.path, {"idempotency_key": "not-submitted"}, **self.headers)
        self.assertIsNone(self.follow_payload(missing)["submission"])
        self.assertEqual((AgentRunFollowUp.objects.count(), self.task.run.messages.count()), before)
        self.assertEqual(self.follow_payload(self.client.get(self.path, **self.headers))["items"][0]["id"], first["id"])
        for key in ("", "x" * 129):
            self.assertEqual(self.client.get(self.path, {"idempotency_key": key}, **self.headers).status_code, 400)

    def test_worker_starts_exactly_one_next_turn_in_same_run_with_new_tokens_and_bill(self):
        self.assertEqual(self.send().status_code, 202)
        self.assertEqual(self.finish(), 1)
        self.assertEqual(follow_ups.dispatch_pending(), 1)
        self.assertEqual(follow_ups.dispatch_pending(), 0)
        self.task.refresh_from_db()
        self.assertEqual(self.task.request_json["turn_index"], 2)
        self.assertEqual(AgentDisplayRun.objects.count(), 1)
        new_token = json.loads(decrypt_secret(self.task.execution.encrypted_payload))["context"]["interaction_token"]
        self.assertNotEqual(new_token, self.token)
        self.assertEqual(self.task.run.messages.filter(content="next").count(), 1)
        self.assertEqual(AgentRuntimeInvocation.objects.filter(display_run_id=self.run_id).count(), 2)
        self.assertEqual(self.inbox("receive").status_code, 404)
        self.assertEqual(AgentRunFollowUp.objects.get().status, "dispatched")
        self.assertEqual(AgentRunFollowUp.objects.get().encrypted_authority, "")

    def test_queue_order_and_cancel(self):
        one = self.follow_payload(self.send(content="one"))["id"]
        self.send(content="two", key="two")
        response = self.client.delete(self.path + one + "/", **self.headers)
        self.assertEqual(response.status_code, 200)
        self.finish()
        self.assertEqual(follow_ups.dispatch_pending(), 1)
        self.assertEqual(self.task.run.messages.filter(content="one").count(), 0)
        self.assertEqual(self.task.run.messages.filter(content="two").count(), 1)

    def test_queue_edit_preserves_original_receipt_and_authority(self):
        identity = self.follow_payload(self.send())["id"]
        before = AgentRunFollowUp.objects.get(pk=identity)
        revision = self.follow_payload(self.client.get(self.path, **self.headers))["queue_revision"]
        response = self.client.patch(self.path + identity + "/", {"content": "edited", "expected_revision": revision}, format="json", **self.headers)
        self.assertEqual(response.status_code, 200, response.content)
        after = AgentRunFollowUp.objects.get(pk=identity)
        self.assertEqual(after.content, "edited")
        self.assertEqual((after.fingerprint, after.encrypted_authority, after.expires_at), (before.fingerprint, before.encrypted_authority, before.expires_at))
        self.assertEqual(self.follow_payload(self.send())["content"], "edited")
        self.assertEqual(AgentRunFollowUp.objects.count(), 1)
        self.assertEqual(self.client.patch(self.path + identity + "/", {"content": "stale", "expected_revision": revision}, format="json", **self.headers).status_code, 409)

    def test_queue_reorder_changes_real_dispatch_order(self):
        one = self.follow_payload(self.send(content="one"))["id"]
        two = self.follow_payload(self.send(content="two", key="two"))["id"]
        revision = self.follow_payload(self.client.get(self.path, **self.headers))["queue_revision"]
        response = self.client.patch(self.path, {"ids": [two, one], "expected_revision": revision}, format="json", **self.headers)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([row["id"] for row in self.follow_payload(response)["items"]], [two, one])
        self.finish()
        self.assertEqual(follow_ups.dispatch_pending(), 1)
        self.assertEqual(self.task.run.messages.filter(content="two").count(), 1)
        self.assertEqual(self.task.run.messages.filter(content="one").count(), 0)
        self.assertEqual(self.client.patch(self.path + two + "/", {"content": "too late", "expected_revision": self.follow_payload(response)["queue_revision"]}, format="json", **self.headers).status_code, 409)

    def test_queue_edit_rejects_invalid_and_non_pending_inputs(self):
        identity = self.follow_payload(self.send())["id"]
        def revision():
            return self.follow_payload(self.client.get(self.path, **self.headers))["queue_revision"]
        for content in ("", "😃" * 8001, [], None):
            self.assertEqual(self.client.patch(self.path + identity + "/", {"content": content, "expected_revision": revision()}, format="json", **self.headers).status_code, 400)
        for ids in ([], [identity, identity], ["foreign"], [False], "wrong"):
            self.assertEqual(self.client.patch(self.path, {"ids": ids, "expected_revision": revision()}, format="json", **self.headers).status_code, 409)
        for status in ("received", "dispatched", "blocked", "expired", "cancelled"):
            AgentRunFollowUp.objects.filter(pk=identity).update(status=status)
            self.assertEqual(self.client.patch(self.path + identity + "/", {"content": "new", "expected_revision": revision()}, format="json", **self.headers).status_code, 409)
        AgentRunFollowUp.objects.filter(pk=identity).update(status="pending", expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.client.patch(self.path + identity + "/", {"content": "new", "expected_revision": revision()}, format="json", **self.headers).status_code, 409)

    def test_unreadable_authority_blocks_queue_without_poisoning_worker(self):
        self.send()
        self.send(content="dependent", key="dependent")
        first = AgentRunFollowUp.objects.order_by("created_at").first()
        first.encrypted_authority = "unreadable-after-key-rotation"
        first.save(update_fields=["encrypted_authority"])
        self.finish()
        self.assertEqual(follow_ups.dispatch_pending(), 0)
        first.refresh_from_db()
        self.assertEqual(first.code, "FOLLOW_UP_RECHECK_REQUIRED")
        self.assertEqual(first.encrypted_authority, "")
        self.assertEqual(AgentRunFollowUp.objects.get(idempotency_key="dependent").code, "FOLLOW_UP_QUEUE_PAUSED")
        self.task.refresh_from_db()
        self.assertEqual(self.task.request_json["turn_index"], 1)

    def test_failed_invocation_blocks_queue_without_replay(self):
        self.send()
        self.finish(fail=True)
        self.assertEqual(follow_ups.dispatch_pending(), 0)
        row = AgentRunFollowUp.objects.get()
        self.assertEqual((row.status, row.code), ("blocked", "PREVIOUS_TURN_FAILED"))
        self.assertIsNone(worker.claim())

    def test_cancel_does_not_start_queued_input(self):
        self.send()
        worker.request_cancel(self.task)
        self.assertEqual(follow_ups.dispatch_pending(), 0)
        self.assertEqual(AgentRunFollowUp.objects.get().status, "blocked")

    def test_expiry_and_changed_context_block(self):
        self.send()
        AgentRunFollowUp.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.finish()
        self.assertEqual(follow_ups.dispatch_pending(), 0)
        self.assertEqual(AgentRunFollowUp.objects.get().status, "expired")

    def test_computer_change_requires_new_consent(self):
        self.send()
        self.finish()
        AgentDisplayRun.objects.filter(pk=self.run_id).update(computer_revision=1)
        self.assertEqual(follow_ups.dispatch_pending(), 0)
        self.assertEqual(AgentRunFollowUp.objects.get().code, "FOLLOW_UP_CONTEXT_CHANGED")

    def test_manual_resume_cannot_revive_failed_turn_queue(self):
        self.send()
        # A definitive tool failure ends the turn. A lost managed transport
        # instead retains a running Run while its current attempt recovers.
        self.finish(tool_error=True)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, AgentExecutionTask.STATUS_FAILED)
        self.assertEqual(self.task.run.status, AgentDisplayRun.STATUS_FAILED)
        response = self.client.post(f"/api/v1/agent-runs/{self.run_id}/resume/", {"content": "recover explicitly"},
            format="json", **self.headers)
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(AgentRunFollowUp.objects.get().code, "PREVIOUS_TURN_FAILED")

    def test_manual_resume_does_not_replace_a_recovering_managed_turn(self):
        self.send()
        self.finish(fail=True)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, AgentExecutionTask.STATUS_WAITING_FOR_RUNTIME)
        self.assertEqual(self.task.run.status, AgentDisplayRun.STATUS_RUNNING)
        response = self.client.post(f"/api/v1/agent-runs/{self.run_id}/resume/",
            {"content": "do not replace recovery"}, format="json", **self.headers)
        self.assertEqual(response.status_code, 409, response.content)
        self.task.refresh_from_db()
        self.assertEqual(self.task.request_json["turn_index"], 1)
        self.assertFalse(self.task.run.messages.filter(content="do not replace recovery").exists())
        self.assertEqual(AgentRuntimeInvocation.objects.filter(display_run=self.task.run).count(), 1)

    def test_recheck_failure_rolls_back_resumed_tokens_messages_and_invocation(self):
        from rest_framework.exceptions import PermissionDenied
        self.send()
        self.finish()
        before = AgentDisplayRun.objects.get(pk=self.run_id).interaction_token_hash
        with mock.patch("apps.agents.runtime_services.enqueue_agent_task", side_effect=PermissionDenied("budget unavailable")):
            self.assertEqual(follow_ups.dispatch_pending(), 0)
        run = AgentDisplayRun.objects.get(pk=self.run_id)
        self.assertEqual(run.interaction_token_hash, before)
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.messages.filter(content="next").count(), 0)
        self.assertEqual(run.runtime_invocations.count(), 1)

    def test_sdk_none_and_malformed_requests(self):
        worker.claim()
        self.inbox("configure", mode="none")
        self.assertEqual(self.send().status_code, 409)
        response = self.client.post(self.path, {"mode": [], "content": "bad", "idempotency_key": "bad", "turn_index": 1}, format="json", **self.headers)
        self.assertEqual(response.status_code, 400)
        self.inbox("configure", mode="steer_and_queue")
        self.assertEqual(self.inbox("acknowledge", message_id="invalid").status_code, 404)

    def test_input_required_keeps_question_independent_from_follow_up(self):
        worker.claim()
        AgentExecutionTask.objects.filter(pk=self.task.pk).update(status="input_required")
        self.inbox("configure", mode="steer_and_queue")
        self.assertEqual(self.send(mode="steer").status_code, 202)
        self.assertEqual(self.inbox("receive").status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "input_required")

    def test_queue_dispatch_rechecks_permissions_and_rolls_back(self):
        self.send()
        self.finish()
        self.caller_a.is_active = False
        self.caller_a.save(update_fields=["is_active"])
        self.assertEqual(follow_ups.dispatch_pending(), 0)
        self.task.refresh_from_db()
        self.assertEqual(self.task.request_json["turn_index"], 1)
        self.assertEqual(self.task.run.messages.filter(content="next").count(), 0)
        self.assertEqual(AgentRuntimeInvocation.objects.filter(display_run_id=self.run_id).count(), 1)

    def test_sdk_negotiation_receipt_and_ack_are_separate_and_idempotent(self):
        self.assertEqual(self.send(mode="steer").status_code, 409)
        worker.claim()
        response = self.inbox("configure", mode="steer_and_queue")
        self.assertEqual(response.status_code, 200, response.content)
        message = self.follow_payload(self.send(mode="steer"))
        response = self.inbox("receive")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.follow_payload(response)["items"][0]["status"], "received")
        self.inbox("receive")
        self.assertEqual(self.task.run.messages.filter(content="next").count(), 1)
        self.assertEqual(self.inbox("acknowledge", message_id=message["id"]).status_code, 200)
        self.assertEqual(self.inbox("acknowledge", message_id=message["id"]).status_code, 200)
        self.assertEqual(self.inbox("acknowledge", message_id=message["id"], status="rejected").status_code, 409)
        self.assertEqual(self.follow_payload(self.inbox("receive"))["items"], [])
        self.assertEqual(AgentRuntimeInvocation.objects.filter(display_run_id=self.run_id).count(), 1)

    def test_unacknowledged_steer_is_not_claimed_applied_after_completion(self):
        lease = worker.claim()
        self.inbox("configure", mode="steer_and_queue")
        self.send(mode="steer")
        self.inbox("receive")
        self.finish(lease)
        follow_ups.dispatch_pending()
        self.assertEqual(AgentRunFollowUp.objects.get().status, "not_applied")


class FollowUpCommittedGuards:
    def test_queue_edit_racing_dispatch_never_changes_dispatched_content(self):
        identity = self.follow_payload(self.send())["id"]
        revision = self.follow_payload(self.client.get(self.path, **self.headers))["queue_revision"]
        self.finish()
        barrier = threading.Barrier(2)
        def edit():
            client = self.follow_thread_client()
            try:
                barrier.wait(timeout=10)
                return client.patch(self.path + identity + "/", {"content": "edited before dispatch", "expected_revision": revision}, format="json", **self.headers).status_code
            finally:
                connections.close_all()
        def dispatch():
            try:
                barrier.wait(timeout=10)
                return follow_ups.dispatch(self.run_id)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            edit_future = pool.submit(edit)
            dispatch_future = pool.submit(dispatch)
            status, dispatched = edit_future.result(), dispatch_future.result()
        self.assertIn(status, (200, 409))
        self.assertTrue(dispatched)
        row = AgentRunFollowUp.objects.get(pk=identity)
        self.assertEqual(row.status, "dispatched")
        self.assertEqual(row.content, "edited before dispatch" if status == 200 else "next")
        self.assertEqual(self.task.run.messages.filter(content=row.content).count(), 1)
        self.assertEqual(self.task.run.runtime_invocations.count(), 2)

    def test_real_sdk_background_receiver_through_cloud_api_then_queue_next_turn(self):
        # Exercise the actual SDK receiver and DRF endpoints, without launching
        # a second Cloud process or substituting a Computer/browser runner.
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "nexus_openwrt/sdk/nexus-agent-sdk-python/src"))
        from nexus_agent.reporting import NexusRunContext
        lease = worker.claim()
        def cloud_transport(request, **kwargs):
            client = APIClient()
            response = client.generic(request.get_method(), urlparse(request.full_url).path,
                data=request.data or b"", content_type="application/json",
                HTTP_X_NEXUS_INTERACTION_TOKEN=request.get_header("X-nexus-interaction-token"))
            self.assertEqual(response.status_code, 200, response.content)
            result = mock.MagicMock()
            result.__enter__.return_value = result
            result.read.return_value = response.content
            connections.close_all()
            return result
        ctx = NexusRunContext(run_id=self.run_id, interaction_token=self.token, turn_index=1,
            checkpoint_url=f"https://cloud.test/api/v1/internal/agent-runs/{self.run_id}/checkpoint/",
            _cloud_opener=cloud_transport)
        async def receive():
            await ctx.aio.inbox.configure("steer_and_queue")
            # Submit on the caller thread, separate from SDK receive execution.
        asyncio.run(receive())
        try:
            self.assertEqual(self.send(mode="steer", content="keep originals").status_code, 202)
            self.assertEqual(self.send(key="next-turn", content="summarize").status_code, 202)
            async def apply():
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    updates = await ctx.aio.inbox.receive_pending()
                    if updates:
                        self.assertEqual(updates[0].content, "keep originals")
                        await updates[0].acknowledge()
                        return
                    await asyncio.sleep(.05)
                self.fail("SDK did not receive Cloud input")
            asyncio.run(apply())
            self.assertEqual(AgentRunFollowUp.objects.get(mode="steer").status, "applied")
        finally:
            ctx.close()
        self.finish(lease)
        self.assertEqual(follow_ups.dispatch_pending(), 1)
        self.task.refresh_from_db()
        self.assertEqual(self.task.request_json["turn_index"], 2)
        self.assertEqual(str(self.task.run_id), self.run_id)

    def test_concurrent_submissions_with_same_key_create_one_message(self):
        barrier = threading.Barrier(2)
        def submit():
            client = self.follow_thread_client()
            try:
                barrier.wait(timeout=10)
                response = client.post(self.path, {"mode": "queue", "content": "one", "turn_index": 1,
                    "idempotency_key": "concurrent"}, format="json", **self.headers)
                return response.status_code, self.follow_payload(response)["id"]
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))
        self.assertEqual({value[0] for value in results}, {202})
        self.assertEqual(len({value[1] for value in results}), 1)
        self.assertEqual(AgentRunFollowUp.objects.count(), 1)

    def test_competing_workers_dispatch_once(self):
        self.send()
        self.finish()
        barrier = threading.Barrier(2)
        def dispatch():
            try:
                barrier.wait(timeout=10)
                return follow_ups.dispatch(self.run_id)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: dispatch(), range(2)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(AgentRuntimeInvocation.objects.filter(display_run_id=self.run_id).count(), 2)
        self.assertEqual(self.task.run.messages.filter(content="next").count(), 1)

