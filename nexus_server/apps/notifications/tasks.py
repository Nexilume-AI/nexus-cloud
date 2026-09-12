from __future__ import annotations

from datetime import timedelta
from functools import wraps

from celery import shared_task
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.db import connection
from django.db.models import Q
from django.conf import settings

from .inbox import queue_push_deliveries, refresh_item
from .models import InboxItem, InboxWorkerCursor
from .policy import backend


def single_reconciler(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        # Session lock spans bounded work without keeping a DB transaction open.
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [782394126])
            if not cursor.fetchone()[0]:
                return {"active_checked": 0, "expired_deleted": 0, "busy": True}
        try:
            return fn(*args, **kwargs)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [782394126])
    return run


def _batch(name, queryset, *, rotating=False):
    queryset = backend().maintenance_queryset(name=name, queryset=queryset)
    limit = max(1, min(500, int(getattr(settings, "NEXUS_INBOX_RECONCILE_BATCH_SIZE", 100))))
    cursor, _ = InboxWorkerCursor.objects.get_or_create(name=name)
    position = dict(cursor.position)
    if not rotating:
        full_at = parse_datetime(position["full_at"]) if position.get("full_at") else timezone.now()
        if not position.get("until") and full_at < timezone.now() - timedelta(days=1):
            # Periodic bounded repair catches bulk updates and long transactions
            # beyond the overlap window. It does not rescan 90 days each minute.
            position = {}
            full_at = timezone.now()
        position["full_at"] = full_at.isoformat()
    if rotating:
        if position.get("after"):
            queryset = queryset.filter(pk__gt=position["after"])
        rows = list(queryset.order_by("pk")[:limit + 1])
    else:
        until = parse_datetime(position["until"]) if position.get("until") else timezone.now()
        queryset = queryset.filter(updated_at__lte=until)
        if position.get("since"):
            queryset = queryset.filter(updated_at__gte=position["since"])
        if position.get("after"):
            queryset = queryset.filter(Q(updated_at__gt=position["at"]) | Q(updated_at=position["at"], pk__gt=position["after"]))
        rows = list(queryset.order_by("updated_at", "pk")[:limit + 1])
    # Advance only after all callbacks succeeded; interrupted batches replay.
    yield from rows[:limit]
    if rotating:
        cursor.position = {"after": str(rows[limit-1].pk) if len(rows) > limit else None}
    elif len(rows) > limit:
        cursor.position = {**position, "until": until.isoformat(), "at": rows[limit-1].updated_at.isoformat(), "after": str(rows[limit-1].pk)}
    else:
        # Overlap handles transactions committing shortly after a checkpoint.
        cursor.position = {"since": (until - timedelta(minutes=5)).isoformat(), "full_at": position["full_at"]}
    cursor.save(update_fields=["position", "updated_at"])


@shared_task(name="apps.notifications.tasks.reconcile_work_inbox")
@single_reconciler
def reconcile_work_inbox() -> dict:
    """Repair missed signals and resolve source-backed items idempotently."""
    from apps.datasets.models import DatasetImportJob
    from apps.jobs.models import Job
    from .sources import record_build, record_dataset_import, record_interaction, record_job, record_recovery_required, record_run
    record_operational_issue = backend().record_operational_issue

    cutoff = timezone.now() - timedelta(days=90)
    from apps.agents.models import AgentDisplayRun, AgentExecutionTask, AgentPythonBuild, AgentRunInteraction
    for interaction in _batch("interactions", AgentRunInteraction.objects.filter(status="pending", expires_at__gt=timezone.now()).select_related("run__agent")):
        record_interaction(interaction)
    for run in _batch("runs", AgentDisplayRun.objects.filter(
        run_kind="invocation", status__in=["completed", "failed"], completed_at__gte=cutoff,
    ).select_related("agent")):
        record_run(run)
    for recovery in _batch("recovery", AgentExecutionTask.objects.filter(status="recovery_required").select_related("run__agent")):
        record_recovery_required(recovery)
    for build in _batch("builds", AgentPythonBuild.objects.filter(
        created_by__isnull=False, status__in=["succeeded", "failed"], completed_at__gte=cutoff,
    ).select_related("agent")):
        record_build(build)
    for job in _batch("jobs", Job.objects.filter(created_by__isnull=False).filter(created_at__gte=cutoff)):
        record_job(job)
    for job in _batch("imports", DatasetImportJob.objects.filter(requested_by__isnull=False).filter(created_at__gte=cutoff).select_related("dataset")):
        record_dataset_import(job)
    backend().reconcile_extra_sources(batch=_batch)
    from apps.agents.models import AgentRuntimeDeployment, EdgeNode
    from apps.mobile.models import MobileDevice
    from apps.providers.models import ProviderRuntimeAccount
    from apps.workspaces.models import WorkspaceConnection
    for node in _batch("nodes", EdgeNode.objects.exclude(status="deleted"), rotating=True):
        status = node.effective_connection_status()
        reason = node.effective_connection_status_reason()
        record_operational_issue(source=node, kind="edge_router_issue",
            active=status == EdgeNode.CONNECTION_DEGRADED or reason == EdgeNode.PRESENCE_REASON_EXPIRED,
            navigation_key="edge_router", recipient_id=node.registered_by_id)
    for runtime in _batch("agent-runtimes", AgentRuntimeDeployment.objects.exclude(status="deleted").select_related("agent", "edge_registration__node"), rotating=True):
        active = runtime.effective_status() == runtime.STATUS_FAILED or runtime.effective_health_status() in {
            runtime.HEALTH_DEGRADED, runtime.HEALTH_UNHEALTHY
        }
        record_operational_issue(source=runtime, kind="agent_runtime_issue", active=active,
            navigation_key="agent_runtime", permission="agent.admin")
    for runtime in _batch("provider-runtimes", ProviderRuntimeAccount.objects.exclude(status="deleted"), rotating=True):
        record_operational_issue(source=runtime, kind="provider_runtime_issue",
            active=runtime.status in {runtime.STATUS_FAILED, runtime.STATUS_UNHEALTHY},
            navigation_key="provider_runtime", permission="provider.admin")
    for connection in _batch("computers", WorkspaceConnection.objects.exclude(status="deleted"), rotating=True):
        record_operational_issue(source=connection, kind="computer_issue",
            active=connection.last_test_status == connection.TEST_FAILED,
            navigation_key="computer", recipient_id=connection.created_by_id)
    for device in _batch("mobiles", MobileDevice.objects.exclude(status="deleted"), rotating=True):
        record_operational_issue(source=device, kind="mobile_issue",
            active=bool(device.last_seen_at and device.online_status == device.ONLINE_OFFLINE),
            navigation_key="mobile", recipient_id=device.created_by_id)
    active = InboxItem.objects.filter(state__in=[InboxItem.STATE_NEEDS_ACTION, InboxItem.STATE_IN_PROGRESS, InboxItem.STATE_FAILED])
    active_checked = 0
    for item in _batch("active-items", active.select_related("tenant"), rotating=True):
        active_checked += 1
        refresh_item(item)
        if item.audience_type == InboxItem.AUDIENCE_ROLE and item.state in {
            InboxItem.STATE_NEEDS_ACTION, InboxItem.STATE_FAILED
        }:
            # Role membership and permission assignments can change without the
            # source object changing. Rebuild only missing, idempotent deliveries.
            queue_push_deliveries(item)
    expired = list(backend().maintenance_queryset(name="expired-items", queryset=InboxItem.objects.filter(
        state__in=[InboxItem.STATE_COMPLETED, InboxItem.STATE_RESOLVED, InboxItem.STATE_CANCELED],
        resolved_at__lt=cutoff)).order_by("resolved_at", "pk").values_list("pk", flat=True)[:500])
    deleted, _ = InboxItem.objects.filter(pk__in=expired).delete()
    return {"active_checked": active_checked, "expired_deleted": deleted}


@shared_task(name="apps.notifications.tasks.deliver_work_inbox_push")
def deliver_work_inbox_push() -> dict:
    from .push import deliver_pending_pushes
    return deliver_pending_pushes()
