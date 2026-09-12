"""Shared durable Task contracts; each host supplies its real authority/database fixture."""
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.agents import task_execution as worker
from apps.agents.models import AgentExecutionTask, AgentRunOperation, AgentTaskExecution, AgentRuntimeInvocation
from apps.agents.runtime_runner import _http_stream_request, RuntimeMCPResult
from apps.common.crypto import decrypt_secret
from apps.notifications.models import InboxItem


class DurableTaskHelpers:
    def create_task(self):
        self.authenticate_durable_caller()
        response = self.client.post(f"/api/v1/agents/{self.agent.pk}/private-runs/",
            {"content": "private-input-never-log"}, format="json", **self._headers(self.project_a))
        self.assertEqual(response.status_code, 202, response.content)
        return AgentExecutionTask.objects.get(run_id=self.durable_payload(response)["run_id"])


class DurableTaskGuards:
    def test_postgres_queue_survives_request_and_hides_payload(self):
        self.assert_durable_database()
        with mock.patch("apps.agents.runtime_services.threading.Thread.start") as start:
            task = self.create_task()
        start.assert_not_called()
        execution = task.execution
        self.assertNotIn("private-input", execution.encrypted_payload)
        payload = json.loads(decrypt_secret(execution.encrypted_payload))
        self.assertNotIn("Authorization", payload["headers"])
        self.assertNotIn("X-Nexus-End-User", payload["headers"])
        restored = worker.restore_request(payload)
        self.assertEqual(restored.user.pk, self.caller_a.pk)
        self.assertGreater(task.expires_at, timezone.now())
        self.assertEqual(worker.claim()[0], str(task.pk))
        self.assertIsNone(worker.claim())

    def test_completion_is_once_and_clears_ciphertext(self):
        task = self.create_task()
        lease = worker.claim()
        result = RuntimeMCPResult(status_code=200, headers={}, body=b'{"jsonrpc":"2.0","result":{"content":[]}}')
        with mock.patch("apps.agents.runtime_services.initialize_private_runtime_mcp_session", return_value={}), \
             mock.patch("apps.agents.runtime_services.close_private_runtime_mcp_session"), \
             mock.patch("apps.agents.runtime_services.call_runtime_mcp", return_value=result) as call:
            worker.execute(*lease)
            worker.execute(*lease)
        self.assertEqual(call.call_count, 1)
        task.refresh_from_db()
        self.assertEqual(task.status, "completed")
        self.assertEqual(task.execution.encrypted_payload, "")

    def test_expired_worker_is_requeued_and_old_worker_is_fenced(self):
        task = self.create_task()
        lease = worker.claim()
        AgentTaskExecution.objects.filter(task=task).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertGreaterEqual(worker.sweep(), 1)
        task.refresh_from_db()
        self.assertEqual(task.error_code, "")
        self.assertEqual(task.status, AgentExecutionTask.STATUS_RECOVERING)
        self.assertTrue(task.execution.encrypted_payload)
        with mock.patch("apps.agents.runtime_services._execute_mcp_task") as call:
            worker.execute(*lease)
        call.assert_not_called()
        replacement = worker.claim()
        self.assertEqual(replacement[0], str(task.pk))
        self.assertNotEqual(replacement[1], lease[1])

    def test_hosted_tls_failure_persists_safe_cause_for_display_refresh(self):
        task = self.create_task()
        lease = worker.claim()
        body = {"jsonrpc": "2.0", "id": "test", "result": {"isError": True,
                "content": [{"type": "text", "text": "Bearer secret endpoint"}],
                "_meta": {"nexus": {"failure": {"code": "RUN_CONTEXT_TLS_FAILED", "message": "private CA detail"}}}}}
        result = RuntimeMCPResult(status_code=200, headers={}, body=json.dumps(body).encode())
        with mock.patch("apps.agents.runtime_services.initialize_private_runtime_mcp_session", return_value={}), \
             mock.patch("apps.agents.runtime_services.close_private_runtime_mcp_session"), \
             mock.patch("apps.agents.runtime_services.call_runtime_mcp", return_value=result):
            worker.execute(*lease)
        task.refresh_from_db()
        self.assertEqual(task.status, "failed")
        self.assertEqual(task.result_json["code"], "RUN_CONTEXT_TLS_FAILED")
        self.assertNotIn("secret", json.dumps(task.result_json))
        self.assertNotIn("private CA", json.dumps(task.result_json))
        from apps.agents.run_failures import failure_payload
        failure = failure_payload(task.run)
        self.assertEqual(failure["domain"], "cloud")
        self.assertNotIn("continue_chat", failure["actions"])

    def test_operation_success_replays_saved_result(self):
        task = self.create_task()
        payload = json.loads(decrypt_secret(task.execution.encrypted_payload))
        token = payload["context"]["interaction_token"]
        prepared = worker.prepare_operation(str(task.run_id), token, {
            "sequence": 1,
            "operation_type": "memory.update",
            "request_digest": "a" * 64,
            "can_reconcile": False,
        })
        worker.finish_operation(str(task.run_id), prepared["id"], token, {
            "status": "succeeded",
            "result": {"value": {"revision": 2}},
        })
        replay = worker.prepare_operation(str(task.run_id), token, {
            "sequence": 1,
            "operation_type": "memory.update",
            "request_digest": "a" * 64,
            "can_reconcile": False,
        })
        self.assertFalse(replay["execute"])
        self.assertEqual(replay["result"], {"value": {"revision": 2}})
        operation = task.operations.get(sequence=1)
        self.assertEqual(operation.result_json, {"encrypted": True})
        self.assertNotIn("revision", operation.result_ciphertext)

    def test_unknown_operation_requires_caller_decision(self):
        task = self.create_task()
        payload = json.loads(decrypt_secret(task.execution.encrypted_payload))
        worker.prepare_operation(str(task.run_id), payload["context"]["interaction_token"], {
            "sequence": 1,
            "operation_type": "external.payment",
            "request_digest": "b" * 64,
            "can_reconcile": False,
        })
        with self.captureOnCommitCallbacks(execute=True):
            self.assertFalse(worker.recover_or_require(str(task.pk), "WORKER_LOST"))
        task.refresh_from_db()
        self.assertEqual(task.status, AgentExecutionTask.STATUS_RECOVERY_REQUIRED)
        self.assertEqual(task.execution.state, "recovery_required")
        self.assertTrue(task.execution.encrypted_payload)
        self.assertEqual(task.operations.get(sequence=1).status, AgentRunOperation.STATUS_OUTCOME_UNKNOWN)
        self.assertTrue(InboxItem.objects.filter(
            source_type="agent_recovery",
            source_id=str(task.pk),
            state=InboxItem.STATE_NEEDS_ACTION,
        ).exists())

    def test_managed_runtime_failure_retains_payload_for_recovery(self):
        task = self.create_task()
        lease = worker.claim()
        with mock.patch("apps.agents.runtime_services._execute_mcp_task", side_effect=ConnectionError("runtime lost")):
            worker.execute(*lease)
        task.refresh_from_db()
        self.assertEqual(task.status, AgentExecutionTask.STATUS_WAITING_FOR_RUNTIME)
        self.assertEqual(task.execution.state, "waiting_for_runtime")
        self.assertTrue(task.execution.encrypted_payload)

    def test_cancel_running_waits_for_acknowledgement(self):
        task = self.create_task()
        lease = worker.claim()
        worker.request_cancel(task)
        task.refresh_from_db()
        self.assertEqual(task.status, "cancel_requested")
        self.assertEqual(task.run.status, "running")
        AgentTaskExecution.objects.filter(task=task).update(cancel_requested_at=timezone.now() - timedelta(seconds=61))
        worker.sweep()
        task.refresh_from_db()
        self.assertEqual(task.error_code, "CANCEL_UNCONFIRMED")
        self.assertEqual(task.status, "failed")

    def test_queued_cancel_never_executes(self):
        task = self.create_task()
        worker.request_cancel(task)
        task.refresh_from_db()
        self.assertEqual(task.status, "cancelled")
        self.assertIsNone(worker.claim())
        self.assertEqual(task.execution.encrypted_payload, "")

    def test_heartbeat_renews_only_existing_tokens_to_deadline(self):
        task = self.create_task()
        lease = worker.claim()
        before = task.run.workspace_capabilities_snapshot
        self.assertTrue(worker.heartbeat(*lease))
        task.run.refresh_from_db()
        self.assertLessEqual(task.run.interaction_token_expires_at, task.expires_at)
        self.assertEqual(task.run.workspace_capabilities_snapshot, before)
        payload = json.loads(decrypt_secret(task.execution.encrypted_payload))
        control = worker.execution_control(str(task.run_id), payload["context"]["interaction_token"])
        self.assertTrue(control["lease_active"])
        worker.request_cancel(task)
        self.assertTrue(worker.execution_control(str(task.run_id), payload["context"]["interaction_token"])["cancel_requested"])

    def test_inactive_principal_cannot_execute(self):
        task = self.create_task()
        lease = worker.claim()
        self.caller_a.is_active = False
        self.caller_a.save(update_fields=["is_active"])
        with mock.patch("apps.agents.runtime_services._execute_mcp_task") as call:
            worker.execute(*lease)
        call.assert_not_called()
        task.refresh_from_db()
        self.assertEqual(task.status, "failed")



class DurableClaimGuards:
    def test_concurrent_workers_claim_each_job_once(self):
        tasks = [self.create_task() for _ in range(4)]
        def take():
            close_old_connections()
            try:
                return worker.claim()
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=8) as executor:
            leases = list(executor.map(lambda _: take(), range(8)))
        self.assertEqual({value[0] for value in leases if value}, {str(task.pk) for task in tasks})
        self.assertEqual(sum(value is not None for value in leases), 4)


class TaskStreamGuards:
    def test_small_sse_event_arrives_before_upstream_eof(self):
        release = threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'data: {"first":true}\n\n')
                self.wfile.flush()
                release.wait(3)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        started = time.monotonic()
        try:
            response = _http_stream_request(url=f"http://127.0.0.1:{server.server_port}/", method="GET", headers={}, body=b"")
            self.assertIn(b"first", next(response.chunks))
            self.assertLess(time.monotonic() - started, 1)
            response.chunks.close()
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join()
