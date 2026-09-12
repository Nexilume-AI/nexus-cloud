from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions, status

from apps.audit.services import write_audit_log
from apps.common.project_scope import scope_queryset_to_current_project
from apps.common.authorization import has_nexus_permission
from apps.tenancy.models import Project, Tenant
from apps.common.request_context import get_tenant_from_request

from .models import Job, JobEvent
from .access import job_access


SENSITIVE_KEY_PARTS = ("password", "secret", "token", "api_key", "apikey", "key", "credential")
SAFE_KEY_NAMES = {"key_prefix", "token_prefix", "public_key", "model_key", "idempotency_key"}


class JobNotFound(exceptions.APIException):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "Job not found."
    default_code = "NOT_FOUND"


def list_jobs(*, request):
    tenant = get_tenant_from_request(request)
    require_job_read(user=request.user, tenant=tenant)
    queryset = job_access().scope(Job.objects.filter(tenant=tenant), request).select_related("tenant", "project").order_by("-created_at")
    job_type = request.query_params.get("job_type", "")
    status_value = request.query_params.get("status", "")
    resource_type = request.query_params.get("resource_type", "")
    resource_id = request.query_params.get("resource_id", "")
    if job_type:
        queryset = queryset.filter(job_type=job_type)
    if status_value:
        queryset = queryset.filter(status=status_value)
    if resource_type:
        queryset = queryset.filter(resource_type=resource_type)
    if resource_id:
        queryset = queryset.filter(resource_id=resource_id)
    return queryset


def get_job(*, request, job_id) -> Job:
    tenant = get_tenant_from_request(request)
    require_job_read(user=request.user, tenant=tenant)
    job = job_access().scope(Job.objects.filter(tenant=tenant, id=job_id), request).select_related("tenant", "project").first()
    if job is None:
        raise JobNotFound()
    return job


def list_job_events(*, request, job_id):
    job = get_job(request=request, job_id=job_id)
    return JobEvent.objects.filter(tenant=job.tenant, job=job).order_by("created_at")


@transaction.atomic
def create_job(
    *,
    request,
    job_type: str,
    resource_type: str,
    resource_id: str = "",
    project: Project | None = None,
    input_json: dict[str, Any] | None = None,
) -> Job:
    tenant = get_tenant_from_request(request)
    job = Job.objects.create(
        tenant=tenant,
        project=project,
        job_type=job_type,
        resource_type=resource_type,
        resource_id=str(resource_id or ""),
        input_json=redact_payload(input_json or {}),
        created_by=request_user_or_none(request.user),
    )
    add_job_event(job=job, event_type="queued", message="Job queued.", metadata_json={"job_type": job_type})
    write_audit_log(
        request=request,
        actor=request.user,
        tenant=tenant,
        action="jobs.create",
        resource_type="job",
        resource_id=job.id,
        after=job_snapshot(job),
    )
    return job


def set_celery_task_id(*, job_id, celery_task_id: str) -> None:
    Job.objects.filter(id=job_id).update(celery_task_id=str(celery_task_id), updated_at=timezone.now())


@transaction.atomic
def start_job(*, job_id, celery_task_id: str = "") -> Job:
    job = locked_job(job_id)
    before = job_snapshot(job)
    job.status = Job.STATUS_RUNNING
    if celery_task_id:
        job.celery_task_id = celery_task_id
    job.started_at = timezone.now()
    job.save(update_fields=["status", "celery_task_id", "started_at", "updated_at"])
    add_job_event(job=job, event_type="running", message="Job started.")
    write_audit_log(
        actor=job.created_by,
        tenant=job.tenant,
        action="jobs.start",
        resource_type="job",
        resource_id=job.id,
        before=before,
        after=job_snapshot(job),
    )
    return job


@transaction.atomic
def succeed_job(*, job_id, result_json: dict[str, Any] | None = None) -> Job:
    job = locked_job(job_id)
    before = job_snapshot(job)
    job.status = Job.STATUS_SUCCEEDED
    job.result_json = redact_payload(result_json or {})
    job.error_code = ""
    job.error_message = ""
    job.completed_at = timezone.now()
    job.save(update_fields=["status", "result_json", "error_code", "error_message", "completed_at", "updated_at"])
    add_job_event(job=job, event_type="succeeded", message="Job succeeded.", metadata_json=job.result_json)
    write_audit_log(
        actor=job.created_by,
        tenant=job.tenant,
        action="jobs.succeed",
        resource_type="job",
        resource_id=job.id,
        before=before,
        after=job_snapshot(job),
    )
    return job


@transaction.atomic
def fail_job(*, job_id, error_code: str, error_message: str, result_json: dict[str, Any] | None = None) -> Job:
    job = locked_job(job_id)
    before = job_snapshot(job)
    job.status = Job.STATUS_FAILED
    job.result_json = redact_payload(result_json or {})
    job.error_code = str(error_code)[:64]
    job.error_message = str(error_message)[:4000]
    job.completed_at = timezone.now()
    job.save(update_fields=["status", "result_json", "error_code", "error_message", "completed_at", "updated_at"])
    add_job_event(job=job, event_type="failed", message=job.error_message[:512], metadata_json={"error_code": job.error_code})
    write_audit_log(
        actor=job.created_by,
        tenant=job.tenant,
        action="jobs.fail",
        resource_type="job",
        resource_id=job.id,
        before=before,
        after=job_snapshot(job),
    )
    return job


def add_job_event(*, job: Job, event_type: str, message: str = "", metadata_json: dict[str, Any] | None = None) -> JobEvent:
    return JobEvent.objects.create(
        tenant=job.tenant,
        job=job,
        event_type=event_type,
        message=message[:512],
        metadata_json=redact_payload(metadata_json or {}),
    )


def locked_job(job_id) -> Job:
    job = (
        Job.objects.select_for_update(of=("self",))
        .select_related("tenant", "project", "created_by")
        .filter(id=job_id)
        .first()
    )
    if job is None:
        raise JobNotFound()
    return job


def redact_payload(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            key_text = str(key)
            if is_sensitive_key(key_text):
                result[key_text] = "***REDACTED***"
            else:
                result[key_text] = redact_payload(item)
        return result
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    return value


def is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if normalized in SAFE_KEY_NAMES:
        return False
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def job_snapshot(job: Job) -> dict[str, Any]:
    return {
        "tenant_id": str(job.tenant_id),
        "project_id": str(job.project_id or ""),
        "job_type": job.job_type,
        "resource_type": job.resource_type,
        "resource_id": job.resource_id,
        "status": job.status,
        "celery_task_id": job.celery_task_id,
        "input_json": redact_payload(job.input_json),
        "result_json": redact_payload(job.result_json),
        "error_code": job.error_code,
    }


def require_job_read(*, user, tenant: Tenant) -> None:
    return job_access().require_read(user=user, tenant=tenant)


def request_user_or_none(principal):
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    return principal if isinstance(principal, user_model) else None
