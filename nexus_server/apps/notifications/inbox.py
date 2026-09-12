from __future__ import annotations

from datetime import timedelta
from typing import Iterable

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import exceptions

from apps.common.subjects import request_subject
from .policy import backend

from .models import InboxItem, InboxReceipt


COPY = {
    "input_required": ("Your answer is needed", "The Agent is waiting for your response.", "Answer in Display"),
    "run_failed": ("Run failed", "Review this Run and its recovery options.", "Review Run"),
    "run_completed": ("Run completed", "Your result is ready.", "View result"),
    "recovery_required": ("Run needs a recovery decision", "Nexus paused an operation whose outcome could not be confirmed.", "Review recovery"),
    "build_failed": ("Python build failed", "Review the build before trying again.", "Review build"),
    "build_succeeded": ("Python build ready", "The candidate image is ready for review.", "View build"),
    "deployment_failed": ("Agent deployment failed", "Review deployment status and recovery options.", "Review deployment"),
    "deployment_succeeded": ("Agent deployed", "The deployment task finished.", "View deployment"),
    "job_queued": ("Background task queued", "Your task is waiting to start.", "View task"),
    "job_running": ("Background task running", "Your task is in progress.", "View task"),
    "job_succeeded": ("Background task completed", "Your task completed successfully.", "View task"),
    "job_failed": ("Background task failed", "Your task needs review.", "Review task"),
    "job_canceled": ("Background task canceled", "The task was canceled.", "View task"),
    "dataset_import_queued": ("Data import queued", "The import is waiting to start.", "Open Data Assets"),
    "dataset_import_running": ("Data import running", "The import is in progress.", "Open Data Assets"),
    "dataset_import_succeeded": ("Data import completed", "The imported asset is ready.", "Open Data Assets"),
    "dataset_import_failed": ("Data import failed", "Review the import and try again.", "Review import"),
    "alert_firing": ("Operations alert needs attention", "A monitored condition is firing.", "Review alert"),
    "alert_delivery_failed": ("Alert delivery failed", "A monitoring notification could not be delivered.", "Review delivery"),
    "report_delivery_failed": ("Report delivery failed", "A scheduled report could not be delivered.", "Review delivery"),
    "approval_required": ("Approval required", "A shared role queue has work ready for review.", "Review request"),
    "edge_router_issue": ("OpenWrt Router needs attention", "Connectivity needs review before dependent work can continue.", "Review Router"),
    "agent_runtime_issue": ("Agent Runtime needs attention", "Runtime health or availability needs review.", "Review Runtime"),
    "provider_runtime_issue": ("Provider Runtime needs attention", "Provider health or availability needs review.", "Review Provider"),
    "computer_issue": ("Computer needs attention", "The latest connection check failed.", "Review Computer"),
    "mobile_issue": ("Mobile needs attention", "The paired Mobile is currently unavailable.", "Review Mobile"),
}

FINAL_STATES = {InboxItem.STATE_COMPLETED, InboxItem.STATE_RESOLVED, InboxItem.STATE_CANCELED}
ACTIVE_STATES = {InboxItem.STATE_NEEDS_ACTION, InboxItem.STATE_IN_PROGRESS, InboxItem.STATE_FAILED}


def user_can_receive_role_item(item: InboxItem, user) -> bool:
    return backend().user_can_receive_role_item(item=item, user=user)


def authorized_project_ids(request, tenant) -> set:
    return backend().authorized_project_ids(request=request, tenant=tenant)


def require_personal_user(request):
    subject = request_subject(request)
    if subject.principal_type != "user":
        raise exceptions.PermissionDenied("Work Inbox requires a signed-in user.")
    return subject


def visible_items(request):
    return backend().visible_items(request=request)


def receipt_for(item, user):
    receipt, _ = InboxReceipt.objects.get_or_create(item=item, user=user)
    return receipt


@transaction.atomic
def upsert_item(*, tenant_id, project_id=None, recipient_id=None, category, kind, state, priority,
                audience_type=InboxItem.AUDIENCE_PERSONAL, required_permission="", source_type,
                source_id, event_key, navigation_key, title_key=None, occurred_at=None,
                due_at=None, resolved_at=None, safe_context=None) -> InboxItem:
    audience_key = f"user:{recipient_id}" if audience_type == InboxItem.AUDIENCE_PERSONAL else f"permission:{required_permission}:{project_id or 'tenant'}"
    defaults = {
        "project_id": project_id,
        "recipient_id": recipient_id,
        "category": category,
        "kind": kind,
        "state": state,
        "priority": priority,
        "audience_type": audience_type,
        "required_permission": required_permission,
        "source_type": source_type,
        "source_id": str(source_id),
        "navigation_key": navigation_key,
        "title_key": title_key or kind,
        "safe_context": safe_context or {},
        "occurred_at": occurred_at or timezone.now(),
        "due_at": due_at,
        "resolved_at": resolved_at,
    }
    item, created = InboxItem.objects.select_for_update().get_or_create(
        tenant_id=tenant_id, event_key=event_key, audience_key=audience_key, defaults=defaults,
    )
    previous_state = item.state
    if not created:
        changed = [field for field, value in defaults.items() if getattr(item, field) != value]
        if changed:
            for field in changed:
                setattr(item, field, defaults[field])
            item.save(update_fields=[*changed, "updated_at"])
    if created or previous_state != state:
        queue_push_deliveries(item, reset=not created)
    return item


def queue_push_deliveries(item: InboxItem, *, reset=False) -> None:
    from .models import PushDelivery, WebPushSubscription
    if item.audience_type == InboxItem.AUDIENCE_PERSONAL:
        user_ids = [item.recipient_id] if item.recipient_id else []
    else:
        user_ids = backend().role_subscriber_ids(item=item)
    subscriptions = WebPushSubscription.objects.filter(user_id__in=user_ids, enabled=True)
    from itertools import islice
    ids = subscriptions.values_list("pk", flat=True).iterator(chunk_size=200)
    while batch := list(islice(ids, 200)):
        PushDelivery.objects.bulk_create(
            [PushDelivery(item=item, subscription_id=pk) for pk in batch], ignore_conflicts=True, batch_size=200,
        )
    if reset:
        # A source state transition is a new notification event for the same
        # stable Inbox item, so even a previously delivered device is eligible.
        PushDelivery.objects.filter(item=item, subscription__in=subscriptions).update(
            status=PushDelivery.STATUS_PENDING, attempts=0, next_attempt_at=timezone.now(), sent_at=None, error_code="",
            lease_token=None, lease_expires_at=None,
        )


def refresh_item(item: InboxItem) -> InboxItem:
    """Reconcile only safe state. Never copy source payload or business content."""
    state = item.state
    resolved_at = item.resolved_at
    try:
        if item.source_type == "run":
            from apps.agents.models import AgentDisplayRun
            source = AgentDisplayRun.objects.filter(pk=item.source_id).only("status", "completed_at").first()
            if source is None:
                state, resolved_at = InboxItem.STATE_RESOLVED, timezone.now()
            elif item.kind == "input_required":
                from apps.agents.models import AgentRunInteraction
                pending = AgentRunInteraction.objects.filter(run=source, status="pending", expires_at__gt=timezone.now()).exists()
                state = InboxItem.STATE_NEEDS_ACTION if pending else InboxItem.STATE_RESOLVED
            else:
                if item.kind == "run_failed" and source.status != "failed":
                    state = InboxItem.STATE_RESOLVED
                else:
                    state = {"running": InboxItem.STATE_IN_PROGRESS, "completed": InboxItem.STATE_COMPLETED,
                             "failed": InboxItem.STATE_FAILED}.get(source.status, state)
                if state == InboxItem.STATE_COMPLETED:
                    resolved_at = source.completed_at
        elif item.source_type == "agent_recovery":
            from apps.agents.models import AgentTaskExecution
            source = AgentTaskExecution.objects.filter(task_id=item.source_id).only("state").first()
            state = (
                InboxItem.STATE_NEEDS_ACTION
                if source and source.state == "recovery_required"
                else InboxItem.STATE_RESOLVED
            )
        elif item.source_type == "job":
            from apps.jobs.models import Job
            source = Job.objects.filter(pk=item.source_id).only("status", "completed_at").first()
            state = _job_state(source.status) if source else InboxItem.STATE_RESOLVED
        elif item.source_type == "dataset_import":
            from apps.datasets.models import DatasetImportJob
            source = DatasetImportJob.objects.filter(pk=item.source_id).only("state").first()
            state = _dataset_state(source.state) if source else InboxItem.STATE_RESOLVED
        elif item.source_type == "alert_event":
            from apps.metrics.models import AlertEvent
            source = AlertEvent.objects.filter(pk=item.source_id).only("status", "resolved_at").first()
            state = InboxItem.STATE_NEEDS_ACTION if source and source.status == "firing" else InboxItem.STATE_RESOLVED
        elif item.source_type in {"alert_delivery", "report_delivery"}:
            from apps.metrics.models import AlertNotification, ReportDelivery
            model = AlertNotification if item.source_type == "alert_delivery" else ReportDelivery
            source = model.objects.filter(pk=item.source_id).only("delivery_status").first()
            state = InboxItem.STATE_FAILED if source and source.delivery_status == "failed" else InboxItem.STATE_RESOLVED
        elif item.source_type == "edge_router_issue":
            from apps.agents.models import EdgeNode
            source = EdgeNode.objects.filter(pk=item.source_id).first()
            active = bool(source and (source.effective_connection_status() == EdgeNode.CONNECTION_DEGRADED
                or source.effective_connection_status_reason() == EdgeNode.PRESENCE_REASON_EXPIRED))
            state = InboxItem.STATE_NEEDS_ACTION if active else InboxItem.STATE_RESOLVED
        elif item.source_type == "agent_runtime_issue":
            from apps.agents.models import AgentRuntimeDeployment
            source = AgentRuntimeDeployment.objects.filter(pk=item.source_id).select_related("edge_registration__node").first()
            active = bool(source and (source.effective_status() == source.STATUS_FAILED or source.effective_health_status() in {
                source.HEALTH_DEGRADED, source.HEALTH_UNHEALTHY
            }))
            state = InboxItem.STATE_NEEDS_ACTION if active else InboxItem.STATE_RESOLVED
        elif item.source_type == "provider_runtime_issue":
            from apps.providers.models import ProviderRuntimeAccount
            source = ProviderRuntimeAccount.objects.filter(pk=item.source_id).first()
            active = bool(source and source.status in {source.STATUS_FAILED, source.STATUS_UNHEALTHY})
            state = InboxItem.STATE_NEEDS_ACTION if active else InboxItem.STATE_RESOLVED
        elif item.source_type == "computer_issue":
            from apps.workspaces.models import WorkspaceConnection
            source = WorkspaceConnection.objects.filter(pk=item.source_id).exclude(status="deleted").first()
            state = InboxItem.STATE_NEEDS_ACTION if source and source.last_test_status == source.TEST_FAILED else InboxItem.STATE_RESOLVED
        elif item.source_type == "mobile_issue":
            from apps.mobile.models import MobileDevice
            source = MobileDevice.objects.filter(pk=item.source_id).exclude(status="deleted").first()
            active = bool(source and source.last_seen_at and source.online_status == source.ONLINE_OFFLINE)
            state = InboxItem.STATE_NEEDS_ACTION if active else InboxItem.STATE_RESOLVED
        else:
            state, resolved_at = backend().refresh_external_item(item=item, state=state, resolved_at=resolved_at)
    except (ValueError, TypeError):
        state, resolved_at = InboxItem.STATE_RESOLVED, timezone.now()
    if state != item.state or resolved_at != item.resolved_at:
        item.state = state
        if state in {InboxItem.STATE_COMPLETED, InboxItem.STATE_RESOLVED, InboxItem.STATE_CANCELED} and not resolved_at:
            resolved_at = timezone.now()
        item.resolved_at = resolved_at
        item.save(update_fields=["state", "resolved_at", "updated_at"])
    return item


def _job_state(value: str) -> str:
    return {"queued": InboxItem.STATE_IN_PROGRESS, "running": InboxItem.STATE_IN_PROGRESS,
            "succeeded": InboxItem.STATE_COMPLETED, "failed": InboxItem.STATE_FAILED,
            "canceled": InboxItem.STATE_CANCELED}.get(value, InboxItem.STATE_RESOLVED)


def _dataset_state(value: str) -> str:
    return {"queued": InboxItem.STATE_IN_PROGRESS, "running": InboxItem.STATE_IN_PROGRESS,
            "processing": InboxItem.STATE_IN_PROGRESS, "succeeded": InboxItem.STATE_COMPLETED,
            "completed": InboxItem.STATE_COMPLETED, "failed": InboxItem.STATE_FAILED,
            "canceled": InboxItem.STATE_CANCELED}.get(value, InboxItem.STATE_IN_PROGRESS)


def navigation_for(item: InboxItem) -> str:
    context = item.safe_context
    routes = {
        "agent_run": lambda: f"/agents/{context['agent_id']}/private-display?run={context.get('run_id') or item.source_id}",
        "agent_runtime": lambda: f"/agents/{context['agent_id']}/runtime",
        "job": lambda: f"/observability?view=activity&mode=jobs&job={item.source_id}",
        "dataset": lambda: f"/data-assets?view=collections&dataset={context.get('dataset_id', '')}",
        "alerts": lambda: f"/observability?view=automations&mode=alerts&event={item.source_id}",
        "reports": lambda: "/observability?view=automations&mode=reports",
        **backend().navigation_routes(item=item),
        "edge_router": lambda: f"/openwrt-routers?router={item.source_id}",
        "provider_runtime": lambda: f"/providers?runtime={item.source_id}",
        "computer": lambda: f"/computer?connection={item.source_id}",
        "mobile": lambda: f"/mobile?device={item.source_id}",
    }
    resolver = routes.get(item.navigation_key)
    if resolver is None:
        raise exceptions.NotFound("Inbox destination is unavailable.")
    try:
        return resolver()
    except (KeyError, ValueError, TypeError):
        raise exceptions.NotFound("Inbox destination is unavailable.") from None


def serialize_item(item: InboxItem, user) -> dict:
    # Listing and summary endpoints must remain read-only. A receipt is created
    # only when the caller explicitly reads, snoozes, archives, or opens work.
    # The query layer annotates caller-bound receipts before the page LIMIT.
    read_at = getattr(item, "_read_at", None)
    snoozed_until = getattr(item, "_snoozed_until", None)
    state = getattr(item, "_live_state", item.state)
    title, message, action = COPY.get(item.title_key, COPY.get(item.kind, ("Work item", "Open the source to continue.", "Open")))
    snoozed = bool(snoozed_until and snoozed_until > timezone.now())
    return {
        "id": str(item.pk), "category": item.category, "kind": item.kind, "state": "snoozed" if snoozed else state,
        "priority": item.priority, "audience": item.audience_type, "ownership": "role" if item.audience_type == "role" else "personal",
        "title": title, "message": message, "action_label": action,
        "project_id": str(item.project_id) if item.project_id else None,
        "project_name": item.project.name if item.project_id else None,
        "resource_name": str(item.safe_context.get("resource_name") or item.safe_context.get("agent_name") or ""),
        "occurred_at": item.occurred_at, "due_at": item.due_at, "resolved_at": item.resolved_at,
        "updated_at": item.updated_at, "read_at": read_at,
        "is_read": read_at is not None, "snoozed_until": snoozed_until,
        "can_archive": state in FINAL_STATES,
    }


def retention_cutoff():
    return timezone.now() - timedelta(days=90)
