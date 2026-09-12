"""Durable Queue and cooperative Steer; no parallel handlers or automatic replay.

Lock order matches task_execution: execution envelope -> Run -> task -> inputs.
Only a successful, finalized turn can consume queued input. Authority and pricing
are rechecked at dispatch, and no caller credential is retained in plaintext.
"""
from dataclasses import asdict
from datetime import timedelta
import hashlib
import json
import uuid

from cryptography.fernet import InvalidToken
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from rest_framework import exceptions

from apps.common.crypto import decrypt_secret, encrypt_secret
from apps.common.subjects import request_subject
from apps.common.invocation_lifecycle import capture_follow_up_authority, recheck_follow_up_authority, verify_follow_up_reservation
from .models import AgentDisplayRun, AgentExecutionTask, AgentRunFollowUp, AgentRunMessage, AgentTaskExecution

MODES = {"none", "queue", "steer_and_queue"}
ACTIVE = {"working", "input_required"}


class FollowUpConflict(exceptions.APIException):
    status_code = 409
    default_code = "FOLLOW_UP_CONFLICT"


def conflict(code):
    raise FollowUpConflict({"code": code})


def locked_run(run_id):
    AgentTaskExecution.objects.select_for_update(of=("self",)).filter(task__run_id=run_id).first()
    return AgentDisplayRun.objects.select_for_update(of=("self",)).select_related("agent").get(pk=run_id)


def turn(task):
    return max(1, int((task.request_json or {}).get("turn_index") or 1))


def serialize(row):
    return {"id": str(row.id), "mode": row.mode, "turn_index": row.turn_index,
        "dispatched_turn": row.dispatched_turn, "content": row.content,
        "status": row.status, "code": row.code, "created_at": row.created_at,
        "received_at": row.received_at, "expires_at": row.expires_at,
        "position": row.position,
        "attachments": (row.attachment_manifest or {}).get("attachments", []),
        "files": (row.attachment_manifest or {}).get("files", []),
        "editable": row.mode == "queue" and row.status == "pending" and row.expires_at > timezone.now()}


def queue_revision(rows):
    # Hash the complete pending queue, not just an individual editor. No secrets.
    values = [(str(row.id), row.position, row.content, row.expires_at.isoformat())
              for row in rows if row.mode == "queue" and row.status == "pending"]
    return hashlib.sha256(json.dumps(sorted(values), ensure_ascii=False).encode()).hexdigest()


def payload(run):
    task = getattr(run, "execution_task", None)
    mode = run.follow_up_mode if task and task.continuable else "none"
    pending = list(run.follow_ups.filter(status__in=["pending", "received"]).order_by("created_at", "id")[:20])
    recent = list(run.follow_ups.exclude(id__in=[row.id for row in pending]).order_by("-created_at", "-id")[:80])
    items = sorted(pending + recent, key=lambda row: (row.created_at, row.id))
    queued = sorted((row for row in pending if row.mode == "queue"), key=lambda row: (row.position, row.created_at, row.id))
    items = [row for row in items if row not in queued] + queued
    return {"mode": mode, "turn_index": turn(task) if task else 1,
        "attachment_protocol": {"queue": 1, "steer": run.follow_up_attachment_protocol},
        "queue_revision": queue_revision(pending), "items": [serialize(row) for row in items]}


def _authority(request, run, task):
    extension = capture_follow_up_authority(run=run, task=task)
    return {"subject": asdict(request_subject(request)), "tenant_id": str(request.tenant_id),
        "project_id": str(getattr(request, "project_id", "") or ""),
        "service_token_id": str(getattr(getattr(request, "service_account_token", None), "id", "") or ""),
        "base_url": request.build_absolute_uri("/"), "request_id": str(getattr(request, "request_id", "") or "")[:128],
        **extension, "computer_revision": run.computer_revision,
        "agent_version": run.agent.current_version}


@transaction.atomic
def submit(*, request, run_id, data):
    from .services import get_private_display_run
    from .runtime_services import get_runtime_use_agent, enforce_api_key_agent_policy
    from apps.common.request_context import get_tenant_from_request
    visible = get_private_display_run(request=request, run_id=run_id)
    run = locked_run(visible.id)
    # Recheck after taking the lock (including concurrent token renewal/hiding).
    get_private_display_run(request=request, run_id=run_id)
    if not isinstance(data, dict) or set(data) - {"mode", "content", "idempotency_key", "turn_index", "attachments", "files"}:
        raise exceptions.ValidationError("Follow-up accepts text, mode, turn_index, idempotency_key and private attachment references only.")
    from . import follow_up_attachments as media
    images, files = media.references(data)
    mode, content, key = data.get("mode"), data.get("content"), data.get("idempotency_key")
    target = data.get("turn_index")
    if not isinstance(mode, str) or mode not in {"queue", "steer"} or not isinstance(content, str) or not 1 <= len(content.strip()) <= 8000:
        raise exceptions.ValidationError("Choose queue/steer and provide 1..8000 characters.")
    if not isinstance(key, str) or not 1 <= len(key) <= 128 or type(target) is not int or target < 1:
        raise exceptions.ValidationError("A stable idempotency_key and turn_index are required.")
    content = content.strip()
    values = [mode, content, target]
    if images or files:
        values += [images, files]
    fingerprint = hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()
    previous = run.follow_ups.filter(idempotency_key=key).first()
    if previous:
        if previous.fingerprint != fingerprint:
            conflict("IDEMPOTENCY_CONFLICT")
        return serialize(previous)
    task = AgentExecutionTask.objects.select_for_update().filter(run=run).first()
    if not task or not task.continuable or run.follow_up_mode == "none":
        conflict("FOLLOW_UP_UNSUPPORTED")
    if task.status not in ACTIVE or run.status != "running" or turn(task) != target:
        conflict("TURN_NO_LONGER_ACTIVE")
    if mode == "steer" and run.follow_up_mode != "steer_and_queue":
        conflict("STEER_UNSUPPORTED")
    if mode == "steer" and (images or files) and run.follow_up_attachment_protocol != 1:
        conflict("STEER_ATTACHMENTS_UNSUPPORTED")
    agent = get_runtime_use_agent(request=request, tenant=get_tenant_from_request(request), agent_id=str(run.agent_id))
    enforce_api_key_agent_policy(api_key=getattr(request, "api_key", None), agent=agent)
    if run.follow_ups.filter(status__in=["pending", "received"]).count() >= 20:
        conflict("FOLLOW_UP_QUEUE_FULL")
    position = (run.follow_ups.aggregate(last=Max("position"))["last"] or 0) + 1
    authority = encrypt_secret(json.dumps(_authority(request, run, task))) if mode == "queue" else ""
    manifest = {}
    if images or files:
        try:
            descriptor, prepared, rows = media.prepare(request=request, run=run, task=task, images=images, files=files)
        except exceptions.NotFound:
            # Same generic result for missing, expired and foreign references.
            # This occurs AFTER the locked idempotency lookup: the client can
            # safely edit a rejected draft instead of treating it as lost delivery.
            conflict("FOLLOW_UP_ATTACHMENTS_UNAVAILABLE")
        from .runtime_services import _private_run_arguments
        from .image_services import attachment_arguments
        from .file_transfers import file_arguments
        _private_run_arguments(descriptor=descriptor, content=content, arguments=None,
            image_arguments=attachment_arguments(prepared), file_arguments=file_arguments(rows))
        try:
            manifest = media.snapshot(run=run, prepared=prepared, rows=rows)
        except exceptions.APIException as exc:
            if exc.status_code in {404, 409}:
                conflict("FOLLOW_UP_ATTACHMENTS_UNAVAILABLE")
            raise
    row = AgentRunFollowUp.objects.create(run=run, mode=mode, content=content, position=position,
        turn_index=target, idempotency_key=key, fingerprint=fingerprint,
        expires_at=timezone.now() + timedelta(hours=24),
        attachment_manifest=manifest, encrypted_authority=authority)
    return serialize(row)


@transaction.atomic
def edit_queue(*, request, run_id, data, message_id=None):
    from .services import get_private_display_run
    from .runtime_services import get_runtime_use_agent, enforce_api_key_agent_policy
    from apps.common.request_context import get_tenant_from_request
    visible = get_private_display_run(request=request, run_id=run_id)
    run = locked_run(visible.id)
    get_private_display_run(request=request, run_id=run_id)
    agent = get_runtime_use_agent(request=request, tenant=get_tenant_from_request(request), agent_id=str(run.agent_id))
    enforce_api_key_agent_policy(api_key=getattr(request, "api_key", None), agent=agent)
    allowed = {"content", "expected_revision"} if message_id else {"ids", "expected_revision"}
    if not isinstance(data, dict) or set(data) != allowed:
        raise exceptions.ValidationError("Supply the change and expected_revision.")
    rows = list(run.follow_ups.filter(mode="queue", status="pending").order_by("position", "created_at", "id"))
    if not isinstance(data["expected_revision"], str) or data["expected_revision"] != queue_revision(rows):
        conflict("FOLLOW_UP_QUEUE_CHANGED")
    now = timezone.now()
    if message_id:
        row = next((row for row in rows if str(row.id) == str(message_id)), None)
        if row is None or row.expires_at <= now:
            conflict("FOLLOW_UP_NOT_EDITABLE")
        content = data["content"]
        if not isinstance(content, str) or not 1 <= len(content.strip()) <= 8000:
            raise exceptions.ValidationError("Provide 1..8000 characters.")
        row.content = content.strip()
        # Fingerprint remains the original POST receipt, so retrying its key
        # cannot overwrite an edit or create another invocation.
        row.save(update_fields=["content"])
    else:
        ids = data["ids"]
        if (not isinstance(ids, list) or len(ids) > 20 or any(not isinstance(value, str) for value in ids)
                or len(set(ids)) != len(ids) or set(ids) != {str(row.id) for row in rows}
                or any(row.expires_at <= now for row in rows)):
            conflict("FOLLOW_UP_QUEUE_CHANGED")
        by_id = {str(row.id): row for row in rows}
        for index, identity in enumerate(ids, 1):
            by_id[identity].position = index
        AgentRunFollowUp.objects.bulk_update(rows, ["position"])
    return payload(run)


@transaction.atomic
def cancel(*, request, run_id, message_id):
    from .services import get_private_display_run
    visible = get_private_display_run(request=request, run_id=run_id)
    run = locked_run(visible.id)
    get_private_display_run(request=request, run_id=run_id)
    row = run.follow_ups.filter(pk=message_id).first()
    if row is None:
        raise exceptions.NotFound()
    if row.status == "cancelled":
        return serialize(row)
    if row.status not in {"pending", "blocked"}:
        conflict("FOLLOW_UP_ALREADY_RECEIVED")
    row.status, row.encrypted_authority, row.completed_at = "cancelled", "", timezone.now()
    row.save(update_fields=["status", "encrypted_authority", "completed_at"])
    return serialize(row)


def _sdk_run(run_id, token, target):
    from .runtime_services import get_internal_interaction_run
    get_internal_interaction_run(run_id=run_id, token=token)
    run = locked_run(run_id)
    get_internal_interaction_run(run_id=run_id, token=token)
    task = AgentExecutionTask.objects.filter(run=run).first()
    execution = AgentTaskExecution.objects.filter(task=task).first() if task else None
    if (run.run_kind != "invocation" or not task or type(target) is not int or turn(task) != target
        or task.status not in ACTIVE or run.status != "running" or task.expires_at <= timezone.now()
        or (execution and (execution.state != "running" or execution.cancel_requested_at
            or not execution.lease_expires_at or execution.lease_expires_at <= timezone.now()))):
        conflict("TURN_NO_LONGER_ACTIVE")
    return run, task


@transaction.atomic
def inbox(*, run_id, token, data):
    if not isinstance(data, dict):
        raise exceptions.ValidationError("Inbox request must be an object.")
    run, task = _sdk_run(run_id, token, data.get("turn_index"))
    action = data.get("action")
    if action == "configure":
        mode = data.get("mode")
        if not isinstance(mode, str) or mode not in MODES or (mode != "none" and not task.continuable):
            conflict("FOLLOW_UP_UNSUPPORTED")
        protocol = data.get("attachment_protocol", 0)
        if type(protocol) is not int or protocol not in {0, 1}:
            raise exceptions.ValidationError("Unsupported follow-up attachment protocol.")
        if protocol == 0 and run.follow_ups.filter(mode="steer", status__in=["pending", "received"]).exclude(attachment_manifest={}).exists():
            conflict("STEER_ATTACHMENTS_PENDING")
        # Never silently orphan queued messages after an SDK downgrade.
        run.follow_up_mode = mode
        run.follow_up_attachment_protocol = protocol
        run.save(update_fields=["follow_up_mode", "follow_up_attachment_protocol"])
        if mode == "none":
            run.follow_ups.filter(status="pending", mode="queue").update(
                status="blocked", code="FOLLOW_UP_UNSUPPORTED", encrypted_authority="")
        return {"mode": mode, "protocol": 1, "attachment_protocol": protocol}
    if run.follow_up_mode != "steer_and_queue":
        conflict("STEER_UNSUPPORTED")
    rows = run.follow_ups.filter(mode="steer", turn_index=turn(task))
    if action == "receive":
        rows.filter(status__in=["pending", "received"], expires_at__lte=timezone.now()).update(status="expired")
        items = list(rows.filter(status__in=["pending", "received"]).order_by("created_at", "id")[:20])
        for row in items:
            if row.status == "pending":
                from .file_transfers import bind_files
                from .models import AgentFileTransfer
                from .follow_up_attachments import restore_images
                try:
                    with transaction.atomic():
                        restore_images(run=run, manifest=row.attachment_manifest or {})
                        ids = [item["file_id"] for item in (row.attachment_manifest or {}).get("files", [])]
                        files = list(AgentFileTransfer.objects.filter(run=run, id__in=ids, state="ready"))
                        if len(files) != len(ids):
                            raise exceptions.NotFound()
                        bind_files(run=run, rows=files, turn_index=turn(task))
                except exceptions.APIException:
                    row.status, row.code, row.completed_at = "rejected", "FOLLOW_UP_ATTACHMENTS_UNAVAILABLE", timezone.now()
                    row.save(update_fields=["status", "code", "completed_at"])
                    continue
                row.status, row.received_at = "received", timezone.now()
                row.save(update_fields=["status", "received_at"])
                sequence = (run.messages.order_by("-sequence").values_list("sequence", flat=True).first() or 0) + 1
                AgentRunMessage.objects.get_or_create(run=run, source_message_id=f"follow-up-{row.id}", defaults={
                    "sequence": sequence, "turn_index": turn(task), "role": "user", "content": row.content,
                    "content_blocks": [{"type": "markdown", "text": row.content}, *(row.attachment_manifest or {}).get("blocks", [])]})
        return {"items": [serialize(row) for row in items]}
    if action == "acknowledge":
        try:
            identity = uuid.UUID(str(data.get("message_id")))
        except (ValueError, TypeError):
            raise exceptions.NotFound() from None
        row = rows.filter(pk=identity).first()
        state = data.get("status", "applied")
        if row is None:
            raise exceptions.NotFound()
        if not isinstance(state, str) or state not in {"applied", "rejected"}:
            raise exceptions.ValidationError("Status must be applied or rejected.")
        if row.status == state:
            return serialize(row)
        if row.status != "received":
            conflict("FOLLOW_UP_ACK_CONFLICT")
        row.status, row.completed_at = state, timezone.now()
        row.save(update_fields=["status", "completed_at"])
        return serialize(row)
    raise exceptions.ValidationError("Unknown inbox action.")


@transaction.atomic
def dispatch(run_id):
    """Called by the existing worker, even when no browser page remains open."""
    from .runtime_services import resume_private_run, get_runtime_use_agent
    from .task_execution import restore_request
    from apps.common.request_context import get_tenant_from_request
    run = locked_run(run_id)
    task = AgentExecutionTask.objects.filter(run=run).first()
    if not task:
        return False
    execution = AgentTaskExecution.objects.filter(task=task).first()
    if task.status in {"working", "input_required", "cancel_requested"} or (execution and execution.state in {"running", "queued"}):
        return False
    now = timezone.now()
    run.follow_ups.filter(mode="steer", status__in=["pending", "received"]).update(
        status="not_applied", code="TURN_FINISHED", completed_at=now)
    if task.status != "completed" or run.status != "completed":
        run.follow_ups.filter(mode="queue", status="pending").update(
            status="blocked", code="PREVIOUS_TURN_FAILED", encrypted_authority="")
        return False
    row = run.follow_ups.filter(mode="queue", status="pending").order_by("position", "created_at", "id").first()
    if row is None:
        return False
    if row.expires_at <= now:
        row.status, row.code = "expired", "FOLLOW_UP_EXPIRED"
    elif run.follow_up_mode == "none" or run.caller_hidden_at:
        row.status, row.code = "blocked", "FOLLOW_UP_UNSUPPORTED"
    else:
        try:
            # Nested savepoint guarantees failed readiness/billing rolls back
            # token rotation, messages, task state and wallet reservations.
            with transaction.atomic():
                authority = json.loads(decrypt_secret(row.encrypted_authority))
                request = restore_request(authority)
                get_runtime_use_agent(request=request, tenant=get_tenant_from_request(request), agent_id=str(run.agent_id))
                recheck_follow_up_authority(run=run, task=task, authority=authority)
                if run.computer_revision != authority["computer_revision"] or run.agent.current_version != authority["agent_version"]:
                    conflict("FOLLOW_UP_CONTEXT_CHANGED")
                resume_private_run(request=request, run_id=str(run.id), content=row.content, _from_follow_up=True,
                    _follow_up_manifest=row.attachment_manifest, _follow_up_id=row.id)
                # Price may change between the preflight and the reservation.
                # Validate the actual reserved snapshot inside the savepoint too.
                verify_follow_up_reservation(run=run, task=task, authority=authority)
            task.refresh_from_db()
            row.status, row.dispatched_turn = "dispatched", turn(task)
        except exceptions.APIException as exc:
            row.status = "blocked"
            row.code = str(exc.detail.get("code")) if isinstance(exc.detail, dict) and "code" in exc.detail else "FOLLOW_UP_RECHECK_REQUIRED"
        except InvalidToken:
            # A rotated encryption key must not poison the shared task worker.
            # Require a fresh caller request; never dispatch without authority.
            row.status, row.code = "blocked", "FOLLOW_UP_RECHECK_REQUIRED"
    row.encrypted_authority, row.completed_at = "", now
    row.save(update_fields=["status", "code", "dispatched_turn", "encrypted_authority", "completed_at"])
    if row.status in {"blocked", "expired"}:
        run.follow_ups.filter(mode="queue", status="pending").update(
            status="blocked", code="FOLLOW_UP_QUEUE_PAUSED", encrypted_authority="")
    return row.status == "dispatched"


def dispatch_pending(limit=50):
    # Keep work bounded; active Runs are excluded to avoid starvation.
    ids = list(AgentRunFollowUp.objects.filter(status__in=["pending", "received"])
        .exclude(run__execution_task__status__in=["working", "input_required", "cancel_requested"])
        .order_by("created_at").values_list("run_id", flat=True)[:limit])
    return sum(dispatch(run_id) for run_id in dict.fromkeys(ids))
