"""Idempotent notifications without prompts, payloads, credentials or arbitrary URLs."""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .models import UserNotification
from .inbox import _job_state, upsert_item
from .models import InboxItem


def __getattr__(name):
    from .policy import backend
    return backend().export_source(name)


def _caller(run):
    if run.run_kind != "invocation" or run.caller_principal_type != "user" or not run.consumer_tenant_id:
        return None
    try:
        return get_user_model().objects.filter(pk=run.caller_principal_id, is_active=True).values_list("pk", flat=True).first()
    except (ValueError, TypeError, ValidationError):
        return None


def record_run(run):
    if run.status not in {"completed", "failed"} or not run.completed_at:
        return
    recipient = _caller(run)
    if recipient is None:
        return
    UserNotification.objects.get_or_create(
        recipient_id=recipient, tenant_id=run.consumer_tenant_id,
        event_key=f"run:{run.pk}:{run.status}:{run.completed_at.isoformat()}",
        defaults={"agent_id": run.agent_id, "run": run, "kind": f"run_{run.status}", "source_at": run.completed_at},
    )
    upsert_item(
        tenant_id=run.consumer_tenant_id, project_id=run.consumer_project_id, recipient_id=recipient,
        category=InboxItem.CATEGORY_AGENT, kind=f"run_{run.status}",
        state=InboxItem.STATE_COMPLETED if run.status == "completed" else InboxItem.STATE_FAILED,
        priority=45 if run.status == "completed" else 80, source_type="run", source_id=run.pk,
        event_key=f"run:{run.pk}:{run.status}:{run.completed_at.isoformat()}", navigation_key="agent_run",
        occurred_at=run.completed_at, resolved_at=run.completed_at if run.status == "completed" else None,
        safe_context={"agent_id": str(run.agent_id), "agent_name": run.agent.name},
    )


def record_interaction(question):
    if question.status != "pending" or question.expires_at <= timezone.now():
        return
    run = question.run
    if run.status not in {"starting", "running", "input_required"}:
        return
    recipient = _caller(run)
    if recipient is None:
        return
    UserNotification.objects.get_or_create(
        recipient_id=recipient, tenant_id=run.consumer_tenant_id, event_key=f"question:{question.pk}",
        defaults={"agent_id": run.agent_id, "run": run, "interaction": question,
                  "kind": "input_required", "source_at": question.created_at},
    )
    upsert_item(
        tenant_id=run.consumer_tenant_id, project_id=run.consumer_project_id, recipient_id=recipient,
        category=InboxItem.CATEGORY_AGENT, kind="input_required", state=InboxItem.STATE_NEEDS_ACTION,
        priority=100, source_type="run", source_id=run.pk, event_key=f"question:{question.pk}",
        navigation_key="agent_run", occurred_at=question.created_at, due_at=question.expires_at,
        safe_context={"agent_id": str(run.agent_id), "agent_name": run.agent.name},
    )


def record_recovery_required(task):
    """Create one content-free personal item only when caller action is required."""

    run = task.run
    recipient = _caller(run)
    if recipient is None or task.status != "recovery_required":
        return
    upsert_item(
        tenant_id=run.consumer_tenant_id,
        project_id=run.consumer_project_id,
        recipient_id=recipient,
        category=InboxItem.CATEGORY_AGENT,
        kind="recovery_required",
        state=InboxItem.STATE_NEEDS_ACTION,
        priority=100,
        source_type="agent_recovery",
        source_id=task.pk,
        event_key=f"recovery:{task.pk}:{task.retry_count}",
        navigation_key="agent_run",
        occurred_at=task.updated_at,
        safe_context={
            "agent_id": str(run.agent_id),
            "agent_name": run.agent.name,
            "run_id": str(run.pk),
        },
    )


@transaction.atomic
def record_build(build):
    if build.status not in {"succeeded", "failed"} or not build.created_by_id or not build.completed_at:
        return
    # A reconciler may have loaded this object before its owner deleted it.
    # Serialize with deletion and re-read the source rather than revive a notice.
    from apps.agents.models import AgentPythonBuild
    build = AgentPythonBuild.objects.select_for_update(of=("self",)).select_related("agent").filter(
        pk=build.pk, status__in=["succeeded", "failed"], created_by__isnull=False,
        completed_at__isnull=False,
    ).first()
    if build is None:
        return
    UserNotification.objects.get_or_create(
        recipient_id=build.created_by_id, tenant_id=build.agent.tenant_id, event_key=f"build:{build.pk}",
        defaults={"agent_id": build.agent_id, "build": build, "kind": f"build_{build.status}", "source_at": build.completed_at},
    )
    upsert_item(
        tenant_id=build.agent.tenant_id, project_id=build.agent.project_id, recipient_id=build.created_by_id,
        category=InboxItem.CATEGORY_BACKGROUND, kind=f"build_{build.status}",
        state=InboxItem.STATE_COMPLETED if build.status == "succeeded" else InboxItem.STATE_FAILED,
        priority=45 if build.status == "succeeded" else 80, source_type="build", source_id=build.pk,
        event_key=f"build:{build.pk}", navigation_key="agent_runtime", occurred_at=build.completed_at,
        resolved_at=build.completed_at if build.status == "succeeded" else None,
        safe_context={"agent_id": str(build.agent_id), "agent_name": build.agent.name},
    )


def record_job(job):
    if not job.created_by_id:
        return
    upsert_item(
        tenant_id=job.tenant_id, project_id=job.project_id, recipient_id=job.created_by_id,
        category=InboxItem.CATEGORY_BACKGROUND, kind=f"job_{job.status}", state=_job_state(job.status),
        priority=75 if job.status == "failed" else 50 if job.status in {"queued", "running"} else 40,
        source_type="job", source_id=job.pk, event_key=f"job:{job.pk}", navigation_key="job",
        occurred_at=job.completed_at or job.started_at or job.created_at,
        resolved_at=job.completed_at if job.status in {"succeeded", "canceled"} else None,
        safe_context={"resource_name": job.job_type},
    )
    if job.job_type not in {"agents.runtime.deploy", "agents.deploy"} or job.status not in {"succeeded", "failed"} or not job.completed_at:
        return
    from apps.agents.models import Agent, AgentRuntimeDeployment
    # Only the server's known Agent deployment jobs, never a URL from job payloads.
    try:
        if job.resource_type == "agent_runtime_deployment":
            runtime = AgentRuntimeDeployment.objects.select_related("agent").filter(pk=job.resource_id, tenant_id=job.tenant_id).first()
            agent = runtime.agent if runtime else None
        elif job.resource_type == "agent":
            agent = Agent.objects.filter(pk=job.resource_id, tenant_id=job.tenant_id).exclude(status="deleted").first()
        else:
            return
    except (ValueError, TypeError, ValidationError):
        return
    if agent is None:
        return
    UserNotification.objects.get_or_create(
        recipient_id=job.created_by_id, tenant_id=job.tenant_id, event_key=f"deployment:{job.pk}",
        defaults={"agent": agent, "job": job, "kind": f"deployment_{job.status}", "source_at": job.completed_at},
    )


def record_dataset_import(import_job):
    if not import_job.requested_by_id:
        return
    project_id = import_job.context_project_id or import_job.dataset.project_id
    state = {"failed": InboxItem.STATE_FAILED, "succeeded": InboxItem.STATE_COMPLETED,
             "completed": InboxItem.STATE_COMPLETED, "canceled": InboxItem.STATE_CANCELED}.get(
        import_job.state, InboxItem.STATE_IN_PROGRESS,
    )
    upsert_item(
        tenant_id=import_job.tenant_id, project_id=project_id or None, recipient_id=import_job.requested_by_id,
        category=InboxItem.CATEGORY_BACKGROUND, kind=f"dataset_import_{import_job.state}", state=state,
        priority=75 if state == InboxItem.STATE_FAILED else 50 if state == InboxItem.STATE_IN_PROGRESS else 40,
        source_type="dataset_import", source_id=import_job.pk, event_key=f"dataset-import:{import_job.pk}",
        navigation_key="dataset", occurred_at=import_job.updated_at,
        resolved_at=import_job.updated_at if state in {InboxItem.STATE_COMPLETED, InboxItem.STATE_CANCELED} else None,
        safe_context={"dataset_id": str(import_job.dataset_id), "resource_name": import_job.dataset.name},
    )


def record_alert_event(event):
    permission = "metrics.manage"
    upsert_item(
        tenant_id=event.tenant_id, category=InboxItem.CATEGORY_OPERATIONS, kind="alert_firing",
        state=InboxItem.STATE_NEEDS_ACTION if event.status == "firing" else InboxItem.STATE_RESOLVED,
        priority=90, audience_type=InboxItem.AUDIENCE_ROLE, required_permission=permission,
        source_type="alert_event", source_id=event.pk, event_key=f"alert-event:{event.pk}",
        navigation_key="alerts", occurred_at=event.triggered_at, resolved_at=event.resolved_at,
        safe_context={"resource_name": event.metric},
    )


def record_delivery(delivery, *, report=False):
    kind = "report_delivery_failed" if report else "alert_delivery_failed"
    state = InboxItem.STATE_FAILED if delivery.delivery_status == "failed" else InboxItem.STATE_RESOLVED
    upsert_item(
        tenant_id=delivery.tenant_id, category=InboxItem.CATEGORY_OPERATIONS, kind=kind,
        state=state, priority=75, audience_type=InboxItem.AUDIENCE_ROLE, required_permission="metrics.manage",
        source_type="report_delivery" if report else "alert_delivery", source_id=delivery.pk,
        event_key=f"{kind}:{delivery.pk}", navigation_key="reports" if report else "alerts",
        occurred_at=delivery.updated_at, resolved_at=delivery.updated_at if state == InboxItem.STATE_RESOLVED else None,
        safe_context={"resource_name": "Scheduled report" if report else "Alert notification"},
    )






def record_operational_issue(*, source, kind, active, navigation_key, permission="", recipient_id=None):
    """Materialize only actionable failures; healthy resources do not create noise."""
    if not recipient_id and not permission:
        return None
    audience = InboxItem.AUDIENCE_PERSONAL if recipient_id else InboxItem.AUDIENCE_ROLE
    audience_key = f"user:{recipient_id}" if recipient_id else f"permission:{permission}:{source.project_id or 'tenant'}"
    event_key = f"operations:{kind}:{source.pk}"
    existing = InboxItem.objects.filter(tenant_id=source.tenant_id, event_key=event_key, audience_key=audience_key).exists()
    if not active and not existing:
        return None
    resource_name = getattr(source, "display_name", "") or getattr(source, "name", "")
    if not resource_name and getattr(source, "agent_id", None):
        resource_name = source.agent.name
    context = {"resource_name": resource_name or kind.replace("_", " ")}
    if getattr(source, "agent_id", None):
        context["agent_id"] = str(source.agent_id)
    return upsert_item(
        tenant_id=source.tenant_id,
        project_id=source.project_id,
        recipient_id=recipient_id,
        category=InboxItem.CATEGORY_OPERATIONS,
        kind=kind,
        state=InboxItem.STATE_NEEDS_ACTION if active else InboxItem.STATE_RESOLVED,
        priority=80,
        audience_type=audience,
        required_permission=permission,
        source_type=kind,
        source_id=source.pk,
        event_key=event_key,
        navigation_key=navigation_key,
        title_key=kind,
        occurred_at=source.updated_at,
        resolved_at=None if active else source.updated_at,
        safe_context=context,
    )
