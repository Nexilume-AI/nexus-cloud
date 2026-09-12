"""PostgreSQL dispatch, fencing and platform-managed logical recovery."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from contextvars import ContextVar
from dataclasses import asdict, replace
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.common.crypto import decrypt_secret, encrypt_secret
from apps.common.execution_request import ExecutionRequest
from apps.common.subjects import RequestSubject, request_subject
from .models import (
    AgentDisplayRun,
    AgentExecutionTask,
    AgentRecoveryAttempt,
    AgentRunOperation,
    AgentRuntimeInvocation,
    AgentTaskExecution,
)
from .context_extension import expired_run_context_fields, renewed_run_context_fields

_lease = ContextVar("agent_execution_lease", default=None)
ACTIVE = [
    AgentExecutionTask.STATUS_WORKING,
    AgentExecutionTask.STATUS_INPUT_REQUIRED,
    AgentExecutionTask.STATUS_CANCEL_REQUESTED,
    AgentExecutionTask.STATUS_WAITING_FOR_RUNTIME,
    AgentExecutionTask.STATUS_RECOVERING,
    AgentExecutionTask.STATUS_RECOVERY_REQUIRED,
]


class ExecutionFenced(BaseException):
    """Must bypass business retry handlers; no stale worker may commit results."""


def task_deadline(tenant):
    from apps.common.resource_limits import capability_state
    seconds = max(60, min(int(getattr(settings, "NEXUS_AGENT_TASK_MAX_SECONDS", 21600)), 86400))
    limit = capability_state(tenant=tenant, code="agents.run_minutes_per_run")["limit"]
    if limit is not None:
        seconds = min(seconds, max(1, int(float(limit) * 60)))
    return timezone.now() + timedelta(seconds=seconds)


@transaction.atomic
def enqueue(*, task, request, body, headers, display_pair):
    subject = request_subject(request)
    if subject.principal_type == "anonymous":
        raise PermissionDenied("Durable calls require an authenticated principal.")
    if len(body) > int(getattr(settings, "NEXUS_AGENT_TASK_INPUT_MAX_BYTES", 2 * 1024 * 1024)):
        raise ValidationError("Agent task input exceeds the durable input limit.")
    payload = {
        "body": base64.b64encode(body).decode("ascii"),
        "context": asdict(display_pair[1]),
        "subject": asdict(subject),
        "tenant_id": str(request.tenant_id),
        "project_id": str(getattr(request, "project_id", "") or ""),
        "service_token_id": str(getattr(getattr(request, "service_account_token", None), "id", "") or ""),
        "base_url": request.build_absolute_uri("/"),
        "request_id": str(getattr(request, "request_id", "") or "")[:128],
        "headers": {k: v for k, v in headers.items() if k.lower() in {
            "content-type", "accept", "mcp-protocol-version",
        }},
    }
    task.expires_at = task_deadline(task.run.consumer_tenant or task.agent.tenant)
    task.save(update_fields=["expires_at", "updated_at"])
    from .runtime_services import begin_runtime_invocation
    begin_runtime_invocation(request=request, tenant=task.run.consumer_tenant,
        agent=task.agent, runtime=task.runtime, api_key=getattr(request, "api_key", None),
        tool_name=task.tool_name, display_run=task.run, turn_index=display_pair[1].turn_index,
        execution_profile_id=display_pair[1].execution_profile,
        execution_model=display_pair[1].execution_model,
        reasoning_effort=display_pair[1].reasoning_effort)
    AgentTaskExecution.objects.update_or_create(task=task, defaults={
        "encrypted_payload": encrypt_secret(json.dumps(payload)), "state": "queued",
        "lease_id": None, "lease_expires_at": None, "heartbeat_at": None,
        "cancel_requested_at": None, "finished_at": None,
    })


def restore_request(payload):
    from .runtime_policy import invoke_runtime_policy
    return invoke_runtime_policy("restore_request", payload=payload)


def assert_execution_lease():
    lease = _lease.get()
    if lease is None:
        return
    task_id, lease_id = lease
    query = AgentTaskExecution.objects.filter(
        task_id=task_id, lease_id=lease_id, state="running",
        lease_expires_at__gt=timezone.now(), task__expires_at__gt=timezone.now(),
    )
    # Hold the fence across any enclosing result/billing transaction.
    if transaction.get_connection().in_atomic_block:
        query = query.select_for_update(of=("self",))
    if not query.exists():
        raise ExecutionFenced()


def _runtime_contract_digest(task, runtime):
    from .tool_catalog import effective_mcp_tools, tool_contract_digest

    descriptor = next(
        (
            item
            for item in effective_mcp_tools(agent=task.agent, runtime=runtime)
            if item["name"] == task.tool_name
        ),
        None,
    )
    return tool_contract_digest(descriptor)


@transaction.atomic
def claim():
    now = timezone.now()
    execution = AgentTaskExecution.objects.select_for_update(skip_locked=True, of=("self",)).filter(
        state="queued",
        task__status__in=[AgentExecutionTask.STATUS_WORKING, AgentExecutionTask.STATUS_RECOVERING],
        task__expires_at__gt=now,
    ).order_by("created_at").first()
    if execution is None:
        return None
    execution.state = "running"
    execution.lease_id = uuid.uuid4()
    execution.heartbeat_at = now
    execution.lease_expires_at = now + timedelta(seconds=90)
    execution.save(update_fields=["state", "lease_id", "heartbeat_at", "lease_expires_at", "updated_at"])
    return str(execution.task_id), str(execution.lease_id)


def heartbeat(task_id, lease_id):
    """Revalidate authority before extending the same (never broader) credentials."""
    from .runtime_services import get_runtime_use_agent, enforce_api_key_agent_policy
    from apps.common.request_context import get_tenant_from_request
    execution = AgentTaskExecution.objects.filter(task_id=task_id, lease_id=lease_id, state="running").first()
    if execution is None:
        return False
    payload = json.loads(decrypt_secret(execution.encrypted_payload))
    request = restore_request(payload)
    agent = get_runtime_use_agent(request=request, tenant=get_tenant_from_request(request), agent_id=str(execution.task.agent_id))
    enforce_api_key_agent_policy(api_key=request.api_key, agent=agent)
    with transaction.atomic():
        execution = AgentTaskExecution.objects.select_for_update().filter(
            task_id=task_id, lease_id=lease_id, state="running", lease_expires_at__gt=timezone.now(),
        ).first()
        if execution is None:
            return False
        task = execution.task
        now = timezone.now()
        if task.expires_at <= now:
            return False
        execution.heartbeat_at = now
        execution.lease_expires_at = min(task.expires_at, now + timedelta(seconds=90))
        execution.save(update_fields=["heartbeat_at", "lease_expires_at", "updated_at"])
        run = task.run
        if not execution.cancel_requested_at:
            expiry = min(task.expires_at, now + timedelta(minutes=5))
            updates = {}
            for field in ("interaction", "workspace_delegate", "browser_delegate", "mobile_delegate"):
                if getattr(run, field + "_token_hash", ""):
                    updates[field + "_token_expires_at"] = expiry
            updates.update(renewed_run_context_fields(run=run, expires_at=expiry))
            AgentDisplayRun.objects.filter(pk=run.pk).update(**updates)
        # Active long calls must not be reclaimed by the billing stale sweeper.
        AgentRuntimeInvocation.objects.filter(display_run=run, status="pending").update(updated_at=now)
        from apps.common.invocation_lifecycle import renew_invocation_lease
        renew_invocation_lease(run=run, expires_at=task.expires_at + timedelta(minutes=5))
    return True


def execute(task_id, lease_id):
    from .runtime_runner import RuntimeDisplayContext
    from .runtime_services import _execute_mcp_task
    close_old_connections()
    marker = _lease.set((task_id, lease_id))
    try:
        assert_execution_lease()
        execution = AgentTaskExecution.objects.select_related("task__run").get(task_id=task_id, lease_id=lease_id)
        payload = json.loads(decrypt_secret(execution.encrypted_payload))
        try:
            request = restore_request(payload)
        except PermissionDenied:
            # Revoked caller authority is terminal, not a recoverable Runtime
            # outage. Never keep or replay its encrypted invocation payload.
            _lease.reset(marker)
            marker = _lease.set(None)
            terminate(task_id, "TASK_AUTHORITY_UNAVAILABLE", lease_id=lease_id)
            return
        from .runtime_services import get_runtime_deployment, _resume_invocation_display_context
        active_runtime = get_runtime_deployment(agent=execution.task.agent, env="prod")
        expected_contract = str(execution.task.request_json.get("tool_contract_digest") or "")
        if (str(execution.task.agent.current_version) != str(execution.task.request_json.get("agent_version"))
            or active_runtime.pk != execution.task.runtime_id
            or (expected_contract and _runtime_contract_digest(execution.task, active_runtime) != expected_contract)):
            raise PermissionDenied("The queued Agent runtime changed; explicit resubmission is required.")
        if not heartbeat(task_id, lease_id):
            raise ExecutionFenced()
        context = RuntimeDisplayContext(**payload["context"])
        if execution.task.retry_count:
            request_data = dict(execution.task.request_json or {})
            context = _resume_invocation_display_context(
                run=execution.task.run,
                request=request,
                tool_name=execution.task.tool_name,
                turn_index=max(int(request_data.get("turn_index") or 1), 1),
                workspace_ceiling=tuple(execution.task.run.workspace_capabilities_snapshot or []),
                execution_profile={
                    "id": str(request_data.get("execution_profile_id") or ""),
                    "model": str(request_data.get("execution_model") or ""),
                },
                reasoning_effort=str(request_data.get("reasoning_effort") or ""),
                input_files=list(getattr(context, "input_files", ()) or ()),
            )
        last_sequence = AgentRunOperation.objects.filter(
            task=execution.task,
            status=AgentRunOperation.STATUS_SUCCEEDED,
        ).order_by("-turn_index", "-sequence").values_list("sequence", flat=True).first() or 0
        context = replace(
            context,
            recovery_managed=execution.task.recovery_protocol >= 1,
            recovery_attempt=execution.task.retry_count,
            recovery_is_replay=execution.task.retry_count > 0,
            recovery_last_committed_operation=last_sequence,
        )
        outcome = _execute_mcp_task(task_id=task_id, request=request, agent_id=str(execution.task.agent_id),
            body=base64.b64decode(payload["body"]), headers=payload["headers"],
            display_pair=(execution.task.run, context))
        with transaction.atomic():
            assert_execution_lease()
            execution.refresh_from_db()
            if execution.cancel_requested_at:
                _lease.reset(marker)
                marker = _lease.set(None)
                acknowledged = outcome == "cancel_acknowledged"
                terminate(task_id, "RUN_CANCELLED" if acknowledged else "CANCEL_UNCONFIRMED", cancelled=acknowledged, lease_id=lease_id)
            else:
                from .runtime_services import finalize_runtime_invocation
                execution.task.refresh_from_db()
                if execution.task.status == "failed":
                    for invocation in AgentRuntimeInvocation.objects.filter(display_run=execution.task.run, status="pending"):
                        finalize_runtime_invocation(request=request, invocation=invocation, succeeded=False,
                            error_code=execution.task.error_code, latency_ms=0)
                AgentTaskExecution.objects.filter(task_id=task_id, lease_id=lease_id).update(
                    state="finished", finished_at=timezone.now(), encrypted_payload="", lease_expires_at=None)
                AgentRecoveryAttempt.objects.filter(
                    task=execution.task,
                    turn_index=max(int((execution.task.request_json or {}).get("turn_index") or 1), 1),
                    attempt=execution.task.retry_count,
                ).update(status="succeeded", completed_at=timezone.now())
    except ExecutionFenced:
        pass
    except Exception:
        # Never log decrypted input, tokens or arbitrary upstream exceptions.
        _lease.reset(marker)
        marker = _lease.set(None)
        recover_or_require(task_id, "TASK_EXECUTION_FAILED", lease_id=lease_id)
    finally:
        _lease.reset(marker)
        close_old_connections()


@transaction.atomic
def request_cancel(task):
    execution = AgentTaskExecution.objects.select_for_update().filter(task=task).first()
    AgentDisplayRun.objects.select_for_update().get(pk=task.run_id)
    task.refresh_from_db(fields=["status"])
    if task.status not in ACTIVE:
        return
    if execution is None or execution.state != "running":
        terminate(str(task.pk), "RUN_CANCELLED", cancelled=True)
        return
    now = timezone.now()
    execution.cancel_requested_at = execution.cancel_requested_at or now
    execution.save(update_fields=["cancel_requested_at", "updated_at"])
    AgentExecutionTask.objects.filter(pk=task.pk, status__in=ACTIVE).update(status="cancel_requested", updated_at=now)
    # Revoke side-effecting platform delegates now, retain interaction control so
    # the SDK can observe cancellation. Completion only follows handler return.
    AgentDisplayRun.objects.filter(pk=task.run_id).update(
        workspace_delegate_token_expires_at=now, browser_delegate_token_expires_at=now,
        mobile_delegate_token_expires_at=now, **expired_run_context_fields(now))
    task.run.interactions.filter(status="pending").update(status="cancelled", updated_at=now)


@transaction.atomic
def terminate(task_id, code, *, cancelled=False, lease_id=None):
    from .runtime_services import finalize_runtime_invocation, finish_invocation_display_run
    execution = AgentTaskExecution.objects.select_for_update().filter(task_id=task_id).first()
    if execution and (execution.state not in {"queued", "running", "waiting_for_runtime", "recovery_required"} or (lease_id and str(execution.lease_id) != str(lease_id))):
        return
    if execution and code == "WORKER_LOST_OUTCOME_UNKNOWN" and execution.lease_expires_at and execution.lease_expires_at > timezone.now():
        return
    task = AgentExecutionTask.objects.filter(pk=task_id).first()
    if task is None:
        return
    AgentDisplayRun.objects.select_for_update().get(pk=task.run_id)
    task = AgentExecutionTask.objects.select_for_update().get(pk=task_id)
    now = timezone.now()
    if code == "TASK_DEADLINE_EXCEEDED" and task.expires_at > now:
        return
    if task.status == "completed" and code in {"TASK_DEADLINE_EXCEEDED", "WORKER_LOST_OUTCOME_UNKNOWN"}:
        # The result and bill committed, but the worker died before Display close.
        if execution:
            execution.state, execution.encrypted_payload = "finished", ""
            execution.finished_at, execution.lease_expires_at = now, None
            execution.save(update_fields=["state", "encrypted_payload", "finished_at", "lease_expires_at", "updated_at"])
        from .services import append_display_event
        from .runtime_services import close_invocation_terminal
        from .mobile_access import close_mobile_run
        close_mobile_run(run=task.run)
        if task.run.status == "running":
            append_display_event(run=task.run, event_type="RUN_FINISHED",
                payload={"threadId": str(task.agent_id), "runId": str(task.run_id)})
        close_invocation_terminal(run=task.run)
        task.run.output_artifacts.filter(snapshot_status="pending").update(
            snapshot_status="failed", snapshot_error="Worker stopped before snapshot completion.")
        return
    if execution:
        execution.state = "cancelled" if cancelled else "failed"
        execution.finished_at = now
        execution.encrypted_payload = ""
        execution.lease_expires_at = None
        execution.save(update_fields=["state", "finished_at", "encrypted_payload", "lease_expires_at", "updated_at"])
    if task.status in ACTIVE:
        task.status = "cancelled" if cancelled else "failed"
        task.error_code = code
        task.completed_at = now
        task.save(update_fields=["status", "error_code", "completed_at", "updated_at"])
    for invocation in AgentRuntimeInvocation.objects.filter(display_run=task.run, status="pending"):
        finalize_runtime_invocation(request=None, invocation=invocation, succeeded=False,
            error_code=code, latency_ms=max(0, int((now - invocation.created_at).total_seconds() * 1000)))
    finish_invocation_display_run(run=task.run, succeeded=False, error_code=code)


def _revoke_attempt_tokens(run_id, now):
    AgentDisplayRun.objects.filter(pk=run_id).update(
        interaction_token_expires_at=now,
        workspace_delegate_token_expires_at=now,
        browser_delegate_token_expires_at=now,
        mobile_delegate_token_expires_at=now,
        **expired_run_context_fields(now),
    )


@transaction.atomic
def recover_or_require(task_id, reason, *, lease_id=None):
    """Fence a lost attempt while retaining its encrypted durable payload."""
    execution = (
        AgentTaskExecution.objects.select_for_update(of=("self",))
        .select_related("task__run", "task__runtime")
        .filter(task_id=task_id)
        .first()
    )
    if execution is None or execution.state not in {"queued", "running"}:
        return False
    if lease_id and execution.lease_id and str(execution.lease_id) != str(lease_id):
        return False
    task = AgentExecutionTask.objects.select_for_update().get(pk=task_id)
    now = timezone.now()
    if task.status == AgentExecutionTask.STATUS_COMPLETED:
        terminate(str(task_id), "WORKER_LOST_OUTCOME_UNKNOWN", lease_id=lease_id)
        return False
    if task.expires_at <= now:
        terminate(str(task_id), "TASK_DEADLINE_EXCEEDED", lease_id=lease_id)
        return False
    if task.recovery_protocol < 1:
        terminate(str(task_id), "LIMITED_RECOVERY", lease_id=lease_id)
        return False
    if task.retry_count >= 3:
        terminate(str(task_id), "RECOVERY_ATTEMPTS_EXHAUSTED", lease_id=lease_id)
        return False

    turn_index = max(int((task.request_json or {}).get("turn_index") or 1), 1)
    inflight = list(
        AgentRunOperation.objects.select_for_update().filter(
            task=task,
            turn_index=turn_index,
            status__in=[
                AgentRunOperation.STATUS_PREPARED,
                AgentRunOperation.STATUS_RUNNING,
                AgentRunOperation.STATUS_OUTCOME_UNKNOWN,
            ],
        )
    )
    unknown = [
        operation
        for operation in inflight
        if operation.status == AgentRunOperation.STATUS_OUTCOME_UNKNOWN
        or not operation.can_reconcile
    ]
    for operation in unknown:
        operation.status = AgentRunOperation.STATUS_OUTCOME_UNKNOWN
        operation.error_code = "OPERATION_OUTCOME_UNKNOWN"
        operation.completed_at = now
        operation.save(update_fields=["status", "error_code", "completed_at", "updated_at"])

    task.retry_count += 1
    task.error_code = "OPERATION_OUTCOME_UNKNOWN" if unknown else ""
    task.status = (
        AgentExecutionTask.STATUS_RECOVERY_REQUIRED
        if unknown
        else AgentExecutionTask.STATUS_WAITING_FOR_RUNTIME
    )
    task.save(update_fields=["retry_count", "error_code", "status", "updated_at"])
    execution.state = "recovery_required" if unknown else "waiting_for_runtime"
    execution.lease_id = None
    execution.lease_expires_at = None
    execution.heartbeat_at = None
    execution.save(update_fields=["state", "lease_id", "lease_expires_at", "heartbeat_at", "updated_at"])
    _revoke_attempt_tokens(task.run_id, now)
    AgentRecoveryAttempt.objects.get_or_create(
        task=task,
        turn_index=turn_index,
        attempt=task.retry_count,
        defaults={
            "reason": str(reason or "WORKER_LOST")[:96],
            "status": execution.state,
            "runtime": task.runtime,
            "agent_version": str((task.request_json or {}).get("agent_version") or "")[:32],
            "tool_contract_digest": str((task.request_json or {}).get("tool_contract_digest") or "")[:64],
        },
    )
    if unknown:
        from apps.notifications.sources import record_recovery_required
        transaction.on_commit(lambda task=task: record_recovery_required(task))
    return not unknown


@transaction.atomic
def reconcile_waiting(limit=100):
    """Promote compatible waiting tasks once their Runtime is healthy again."""
    from .runtime_services import AgentRuntimeNotAvailable, get_runtime_deployment

    rows = list(
        AgentTaskExecution.objects.select_for_update(skip_locked=True)
        .select_related("task__agent", "task__run")
        .filter(state="waiting_for_runtime", task__expires_at__gt=timezone.now())
        .order_by("updated_at")[:limit]
    )
    promoted = 0
    for execution in rows:
        task = execution.task
        expected_version = str((task.request_json or {}).get("agent_version") or "")
        if expected_version and str(task.agent.current_version or "") != expected_version:
            execution.state = "recovery_required"
            task.status = AgentExecutionTask.STATUS_RECOVERY_REQUIRED
            task.error_code = "RECOVERY_VERSION_CHANGED"
            execution.save(update_fields=["state", "updated_at"])
            task.save(update_fields=["status", "error_code", "updated_at"])
            from apps.notifications.sources import record_recovery_required
            transaction.on_commit(lambda task=task: record_recovery_required(task))
            continue
        try:
            runtime = get_runtime_deployment(agent=task.agent, env="prod")
        except AgentRuntimeNotAvailable:
            continue
        expected_contract = str((task.request_json or {}).get("tool_contract_digest") or "")
        if expected_contract and _runtime_contract_digest(task, runtime) != expected_contract:
            execution.state = "recovery_required"
            task.status = AgentExecutionTask.STATUS_RECOVERY_REQUIRED
            task.error_code = "RECOVERY_TOOL_CONTRACT_CHANGED"
            execution.save(update_fields=["state", "updated_at"])
            task.save(update_fields=["status", "error_code", "updated_at"])
            from apps.notifications.sources import record_recovery_required
            transaction.on_commit(lambda task=task: record_recovery_required(task))
            continue
        task.runtime = runtime
        task.status = AgentExecutionTask.STATUS_RECOVERING
        task.error_code = ""
        task.save(update_fields=["runtime", "status", "error_code", "updated_at"])
        execution.state = "queued"
        execution.save(update_fields=["state", "updated_at"])
        task.recovery_attempts.filter(turn_index=(task.request_json or {}).get("turn_index", 1), attempt=task.retry_count).update(
            status="recovering", runtime=runtime
        )
        promoted += 1
    return promoted


def sweep(limit=100):
    from .run_capacity import expire_stale_non_invocation_runs

    now = timezone.now()
    expired = list(AgentTaskExecution.objects.filter(state__in=["queued", "running"]).filter(
        Q(task__expires_at__lte=now) | Q(state="running", lease_expires_at__lte=now)
        | Q(cancel_requested_at__lte=now - timedelta(seconds=60))
    ).values_list("task_id", "lease_id", "cancel_requested_at", "task__expires_at")[:limit])
    for task_id, lease_id, cancellation, deadline in expired:
        if cancellation or deadline <= now:
            terminate(str(task_id), "CANCEL_UNCONFIRMED" if cancellation else "TASK_DEADLINE_EXCEEDED", lease_id=lease_id)
        else:
            recover_or_require(str(task_id), "WORKER_LEASE_EXPIRED", lease_id=lease_id)
    legacy = list(AgentExecutionTask.objects.filter(execution__isnull=True, status__in=ACTIVE,
        expires_at__lte=now).values_list("pk", flat=True)[:limit])
    for task_id in legacy:
        terminate(str(task_id), "LEGACY_TASK_EXPIRED")
    return (
        len(expired)
        + len(legacy)
        + reconcile_waiting(limit=limit)
        + expire_stale_non_invocation_runs(now=now)
    )


def execution_control(run_id, token):
    from .runtime_services import get_internal_interaction_run
    run = get_internal_interaction_run(run_id=run_id, token=token)
    task = AgentExecutionTask.objects.filter(run=run).first()
    execution = AgentTaskExecution.objects.filter(task=task).first() if task else None
    return {
        "managed": bool(execution is not None and task and task.recovery_protocol >= 1),
        "recovery_protocol": int(task.recovery_protocol if task else 0),
        "recovery_attempt": int(task.retry_count if task else 0),
        "is_replay": bool(task and task.status == AgentExecutionTask.STATUS_RECOVERING),
        "last_committed_operation": (
            AgentRunOperation.objects.filter(task=task, status=AgentRunOperation.STATUS_SUCCEEDED)
            .order_by("-turn_index", "-sequence")
            .values_list("sequence", flat=True)
            .first()
            if task else None
        ),
        "follow_up_protocol": 1 if task and run.run_kind == "invocation" else 0,
        "state": execution.state if execution else run.status,
        "cancel_requested": bool(execution and execution.cancel_requested_at),
        "deadline": task.expires_at.isoformat() if task else None,
        "remaining_seconds": max(0, int((task.expires_at - timezone.now()).total_seconds())) if task else None,
        "lease_active": bool(execution and execution.state == "running" and execution.lease_expires_at and execution.lease_expires_at > timezone.now()),
    }


def _operation_payload_size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _operation_result(operation):
    if operation.result_ciphertext:
        try:
            value = json.loads(decrypt_secret(operation.result_ciphertext))
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError):
            return {}
    # Transitional fallback for operations committed between migrations 0052
    # and 0054. New operation results are never stored in plaintext.
    return dict(operation.result_json or {})


@transaction.atomic
def prepare_operation(run_id, token, data):
    """Prepare or replay one stable SDK operation."""
    from .runtime_services import get_internal_interaction_run

    run = get_internal_interaction_run(run_id=run_id, token=token)
    task = AgentExecutionTask.objects.select_for_update().filter(run=run).first()
    if task is None or task.recovery_protocol < 1:
        raise ValidationError({"code": "MANAGED_RECOVERY_UNAVAILABLE", "detail": "Managed recovery is unavailable for this Agent version."})
    sequence = data.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1 or sequence > 10000:
        raise ValidationError({"sequence": "Provide a sequence between 1 and 10000."})
    operation_type = str(data.get("operation_type") or "").strip()
    digest = str(data.get("request_digest") or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,95}", operation_type):
        raise ValidationError({"operation_type": "Invalid operation type."})
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValidationError({"request_digest": "A SHA-256 request digest is required."})
    turn_index = max(int((task.request_json or {}).get("turn_index") or 1), 1)
    operation = AgentRunOperation.objects.select_for_update().filter(
        run=run, turn_index=turn_index, sequence=sequence
    ).first()
    if operation is not None:
        if operation.operation_type != operation_type or operation.request_digest != digest:
            task.status = AgentExecutionTask.STATUS_RECOVERY_REQUIRED
            task.error_code = "RECOVERY_DIVERGED"
            task.save(update_fields=["status", "error_code", "updated_at"])
            AgentTaskExecution.objects.filter(task=task).update(state="recovery_required", updated_at=timezone.now())
            from apps.notifications.sources import record_recovery_required
            transaction.on_commit(lambda task=task: record_recovery_required(task))
            raise ValidationError({"code": "RECOVERY_DIVERGED", "detail": "Recovered execution diverged from its operation journal."})
        return {
            "id": str(operation.id),
            "status": operation.status,
            "replayed": True,
            "execute": operation.status not in {AgentRunOperation.STATUS_SUCCEEDED, AgentRunOperation.STATUS_OUTCOME_UNKNOWN},
            "result": _operation_result(operation) if operation.status == AgentRunOperation.STATUS_SUCCEEDED else None,
            "idempotency_key": operation.idempotency_key,
        }
    key_material = f"{run.id}:{turn_index}:{sequence}:{digest}".encode("utf-8")
    operation = AgentRunOperation.objects.create(
        run=run,
        task=task,
        turn_index=turn_index,
        sequence=sequence,
        recovery_generation=task.retry_count,
        operation_type=operation_type,
        request_digest=digest,
        idempotency_key="nxo_" + hashlib.sha256(key_material).hexdigest(),
        status=AgentRunOperation.STATUS_RUNNING,
        can_reconcile=bool(data.get("can_reconcile")),
        started_at=timezone.now(),
    )
    return {
        "id": str(operation.id),
        "status": operation.status,
        "replayed": False,
        "execute": True,
        "result": None,
        "idempotency_key": operation.idempotency_key,
    }


@transaction.atomic
def finish_operation(run_id, operation_id, token, data):
    from .runtime_services import get_internal_interaction_run

    run = get_internal_interaction_run(run_id=run_id, token=token)
    operation = AgentRunOperation.objects.select_for_update().filter(run=run, id=operation_id).first()
    if operation is None:
        raise ValidationError({"code": "OPERATION_NOT_FOUND", "detail": "Managed operation not found."})
    if operation.status == AgentRunOperation.STATUS_SUCCEEDED:
        return {"id": str(operation.id), "status": operation.status, "result": _operation_result(operation)}
    status = str(data.get("status") or "succeeded")
    if status not in {AgentRunOperation.STATUS_SUCCEEDED, AgentRunOperation.STATUS_FAILED, AgentRunOperation.STATUS_OUTCOME_UNKNOWN}:
        raise ValidationError({"status": "Invalid operation completion status."})
    result = data.get("result") or {}
    if not isinstance(result, dict) or _operation_payload_size(result) > 65536:
        raise ValidationError({"result": "Operation result must be a JSON object no larger than 64 KiB."})
    operation.status = status
    operation.result_ciphertext = (
        encrypt_secret(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        if status == AgentRunOperation.STATUS_SUCCEEDED
        else ""
    )
    operation.result_json = {"encrypted": True} if status == AgentRunOperation.STATUS_SUCCEEDED else {}
    operation.error_code = str(data.get("error_code") or "")[:96]
    operation.completed_at = timezone.now()
    operation.save(update_fields=["status", "result_json", "result_ciphertext", "error_code", "completed_at", "updated_at"])
    return {"id": str(operation.id), "status": operation.status, "result": result if status == AgentRunOperation.STATUS_SUCCEEDED else {}}


@transaction.atomic
def recovery_decision(*, run, action):
    task = AgentExecutionTask.objects.select_for_update().filter(run=run).first()
    execution = AgentTaskExecution.objects.select_for_update().filter(task=task).first() if task else None
    if task is None or execution is None or task.status != AgentExecutionTask.STATUS_RECOVERY_REQUIRED:
        raise ValidationError({"code": "RECOVERY_NOT_REQUIRED", "detail": "This Run does not need a recovery decision."})
    if action == "check_status":
        return {
            "state": execution.state,
            "attempt": task.retry_count,
            "unknown_operations": task.operations.filter(status=AgentRunOperation.STATUS_OUTCOME_UNKNOWN).count(),
        }
    if action == "cancel":
        terminate(str(task.id), "RUN_CANCELLED", cancelled=True)
        return {"state": "cancelled"}
    if action != "retry":
        raise ValidationError({"action": "Choose check_status, retry or cancel."})
    AgentRunOperation.objects.filter(
        task=task, status=AgentRunOperation.STATUS_OUTCOME_UNKNOWN
    ).update(
        status=AgentRunOperation.STATUS_PREPARED,
        error_code="CALLER_ACCEPTED_RETRY_RISK",
        completed_at=None,
        updated_at=timezone.now(),
    )
    task.status = AgentExecutionTask.STATUS_WAITING_FOR_RUNTIME
    task.error_code = ""
    task.save(update_fields=["status", "error_code", "updated_at"])
    execution.state = "waiting_for_runtime"
    execution.save(update_fields=["state", "updated_at"])
    reconcile_waiting(limit=1)
    return {"state": AgentTaskExecution.objects.get(task=task).state}
