"""Shared operational indexes, independent of reporting and finance models."""
from uuid import UUID
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from . import policy
from .catalog import respond


def identifier(value):
    try:
        return UUID(value)
    except (ValueError, TypeError):
        raise NotFound("Monitoring record not found.")


def scoped(request, model, action="metrics.read"):
    tenant, project = policy.context(request, action)
    qs = model.objects.filter(tenant=tenant)
    return qs.filter(project_id=project) if project else qs


def job_data(row):
    return {"id": str(row.pk), "tenant_id": str(row.tenant_id), "project_id": str(row.project_id or ""),
        "job_type": row.job_type, "resource_type": row.resource_type, "resource_id": row.resource_id,
        "status": row.status, "error_code": row.error_code, "error_message": "Job failed; inspect the owning resource." if row.error_code else "",
        "started_at": row.started_at, "completed_at": row.completed_at, "created_at": row.created_at, "updated_at": row.updated_at,
        "input_json": {}, "result_json": {}, "celery_task_id": ""}


class MonitoringJobsView(APIView):
    def get(self, request, job_id=None):
        from apps.jobs.models import Job
        qs = scoped(request, Job).defer("input_json", "result_json", "error_message")
        if job_id:
            row = qs.filter(pk=identifier(job_id)).first()
            if row is None:
                raise NotFound()
            return Response(job_data(row), headers={"Cache-Control": "private, no-store"})
        if request.query_params.get("window_seconds"):
            from datetime import timedelta
            from django.db.models import Q
            try:
                seconds = int(request.query_params["window_seconds"])
                if not 60 <= seconds <= 604800:
                    raise ValueError()
            except (TypeError, ValueError):
                raise ValidationError({"window_seconds": "Use 60–604800 seconds."})
            qs = qs.filter(Q(created_at__gte=timezone.now() - timedelta(seconds=seconds)) | Q(status__in=["queued", "running"]))
        for field in ("job_type", "resource_type", "resource_id"):
            if request.query_params.get(field):
                qs = qs.filter(**{field: request.query_params[field]})
        return respond(request, qs, None, search_fields=("job_type", "resource_type", "resource_id"), serialize=lambda rows: [job_data(row) for row in rows])


class MonitoringJobEventsView(APIView):
    def get(self, request, job_id):
        from apps.jobs.models import Job, JobEvent
        job = scoped(request, Job).filter(pk=identifier(job_id)).first()
        if job is None:
            raise NotFound()
        # Arbitrary worker messages/metadata may contain prompts or SSH paths. Only expose event identity/state.
        return respond(request, JobEvent.objects.filter(job=job, tenant=job.tenant), None,
            serialize=lambda rows: [{"id": str(row.pk), "job_id": str(job.pk), "tenant_id": str(job.tenant_id),
                "event_type": row.event_type, "message": row.event_type, "metadata_json": {}, "created_at": row.created_at} for row in rows])


class MonitoringAuditView(APIView):
    def get(self, request, log_id=None):
        from apps.audit.models import AuditLog
        from apps.audit.services import action_filter_values, canonical_action
        tenant, project = policy.context(request, "audit.read")
        qs = AuditLog.objects.filter(tenant_id=str(tenant.pk)).select_related("actor").defer("before_snapshot", "after_snapshot", "metadata", "user_agent")
        if project:
            qs = qs.filter(project_id=project)
        def serialize(row):
            return {"id": str(row.pk), "action": canonical_action(row.action), "actor_id": str(row.actor_id or ""),
                "actor_email": row.actor.email if row.actor else "", "resource_type": row.resource_type, "resource_id": row.resource_id,
                "request_id": row.request_id, "created_at": row.created_at, "updated_at": row.updated_at,
                "before_snapshot": {}, "after_snapshot": {}, "metadata": {"payload_policy": "Operational index only; raw snapshots are not exposed here."}}
        if log_id:
            row = qs.filter(pk=identifier(log_id)).first()
            if row is None:
                raise NotFound()
            return Response(serialize(row), headers={"Cache-Control": "private, no-store"})
        if request.query_params.get("action"):
            qs = qs.filter(action__in=action_filter_values(request.query_params["action"]))
        for field in ("resource_type", "resource_id", "request_id"):
            if request.query_params.get(field):
                qs = qs.filter(**{field: request.query_params[field]})
        return respond(request, qs, None, search_fields=("action", "resource_type", "resource_id", "request_id", "actor__email"), serialize=lambda rows: [serialize(row) for row in rows])
