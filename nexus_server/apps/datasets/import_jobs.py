"""Resumable import orchestration; storage I/O never holds a database lock.

The database is the durable queue, not the broker. A fenced lease prevents a
crashed/recovered or cancelled worker from committing a duplicate file.
"""
from contextvars import ContextVar
from datetime import timedelta
import hashlib
import json
import time
from types import SimpleNamespace
import uuid

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import exceptions

from apps.tenancy.models import Tenant
from .models import Dataset, DatasetImportJob


_attempt = ContextVar("dataset_import_attempt", default=None)
LEASE = timedelta(minutes=5)
MAX_ATTEMPTS = 3


class ImportConflict(exceptions.APIException):
    status_code = 409
    default_detail = "This import request key was already used for different inputs."


class LostLease(Exception):
    pass


def _owner(request):
    # Interactive import jobs intentionally persist a user ID, never a bearer
    # token. Existing machine-identity synchronous endpoints remain compatible.
    from django.contrib.auth import get_user_model
    if not isinstance(request.user, get_user_model()) or not request.user.is_active:
        raise exceptions.PermissionDenied("Background imports require an active user session.")
    return request.user


def enqueue(request, dataset_id, kind, inputs, request_key):
    from .agent_asset_services import resolve_export_context
    from .serializers import AgentTraceExportSerializer, AgentMemoryExportSerializer, AgentArtifactCaptureSerializer
    user = _owner(request)
    validators = {"trace": AgentTraceExportSerializer, "memory": AgentMemoryExportSerializer, "artifact": AgentArtifactCaptureSerializer}
    if not isinstance(kind, str) or kind not in validators:
        raise exceptions.ValidationError({"kind": "Choose trace, memory or artifact."})
    if not request_key or len(request_key) > 128:
        raise exceptions.ValidationError({"request_key": "Supply an idempotency key of 1–128 characters."})
    serializer = validators[kind](data=inputs)
    serializer.is_valid(raise_exception=True)
    values = json.loads(json.dumps(serializer.validated_data, default=str))
    if "memory_item_ids" in values:
        values["memory_item_ids"] = sorted(set(values["memory_item_ids"]))
    dataset, _ = resolve_export_context(request=request, dataset_id=dataset_id, agent_id=values["agent_id"])
    project_id = str(getattr(request, "project_id", "") or "")
    digest = hashlib.sha256(json.dumps([kind, values, project_id], sort_keys=True).encode()).hexdigest()
    with transaction.atomic():
        Tenant.objects.select_for_update(no_key=True).get(pk=dataset.tenant_id)
        Dataset.objects.select_for_update(no_key=True).get(pk=dataset.pk, status="active")
        prior = DatasetImportJob.objects.filter(dataset=dataset, requested_by=user, request_key=request_key).first()
        if prior:
            if prior.request_hash != digest:
                raise ImportConflict()
            return prior
        if DatasetImportJob.objects.filter(tenant=dataset.tenant, state__in=["queued", "running"]).count() >= int(getattr(settings, "NEXUS_DATASET_IMPORT_PENDING_LIMIT", 100)):
            raise exceptions.ValidationError("Too many pending imports in this Organization. Wait for an import to finish.")
        return DatasetImportJob.objects.create(tenant=dataset.tenant, dataset=dataset, requested_by=user,
            context_project_id=project_id, request_key=request_key, request_hash=digest, kind=kind, inputs=values)


def owned_jobs(request, dataset_id):
    from .services import get_mutable_dataset
    user = _owner(request)
    dataset = get_mutable_dataset(request=request, dataset_id=dataset_id)
    return DatasetImportJob.objects.filter(dataset=dataset, requested_by=user).order_by("-created_at", "-id")


def change_job(request, dataset_id, job_id, action):
    with transaction.atomic():
        job = owned_jobs(request, dataset_id).select_for_update().filter(pk=job_id).first()
        if not job:
            raise exceptions.NotFound()
        if action == "retry" and job.state in {"failed", "cancelled"}:
            if job.attempts >= MAX_ATTEMPTS:
                raise exceptions.ValidationError("Retry limit reached. Review the source and create a new import.")
            job.state, job.stage, job.error_code = "queued", "queued", ""
        elif action == "cancel" and job.state in {"queued", "running"}:
            job.state, job.stage = "cancelled", "cancelled"
        else:
            raise exceptions.ValidationError("This action is not available for the current import state.")
        job.lease_id, job.lease_expires_at = None, None
        job.save()
        return job


def payload(job):
    return {"id": str(job.pk), "dataset_id": str(job.dataset_id), "kind": job.kind,
        "state": job.state, "stage": job.stage, "bytes_processed": job.bytes_processed,
        "total_bytes": job.total_bytes, "attempts": job.attempts, "error_code": job.error_code,
        "can_retry": job.state in {"failed", "cancelled"} and job.attempts < MAX_ATTEMPTS,
        "file_id": str(job.file_id) if job.file_id else None,
        "created_at": job.created_at.isoformat(), "updated_at": job.updated_at.isoformat()}


class Attempt:
    def __init__(self, job):
        self.job = job
        self.last_update = 0.0

    def heartbeat(self, stage, processed=0, total=None, force=False):
        now = time.monotonic()
        if not force and now - self.last_update < 5:
            return
        count = DatasetImportJob.objects.filter(pk=self.job.pk, state="running", lease_id=self.job.lease_id,
            lease_expires_at__gt=timezone.now()).update(stage=stage, bytes_processed=processed, total_bytes=total,
                lease_expires_at=timezone.now() + LEASE, updated_at=timezone.now())
        if not count:
            raise LostLease()
        self.last_update = now

    def commit(self, file):
        # Called inside the SAME short transaction as DatasetFile and counters.
        job = DatasetImportJob.objects.select_for_update().get(pk=self.job.pk)
        if job.state != "running" or job.lease_id != self.job.lease_id or job.lease_expires_at <= timezone.now():
            raise LostLease()
        job.file, job.state, job.stage = file, "completed", "completed"
        job.bytes_processed = job.total_bytes = file.size_bytes
        job.lease_id = job.lease_expires_at = None
        job.error_code = ""
        job.save()


def progress(stage, processed=0, total=None, force=False):
    attempt = _attempt.get()
    if attempt:
        attempt.heartbeat(stage, processed, total, force)


def commit_import(file):
    attempt = _attempt.get()
    if attempt:
        attempt.commit(file)


def run_job(job_id):
    with transaction.atomic():
        job = DatasetImportJob.objects.select_for_update(of=("self",)).select_related("requested_by").filter(pk=job_id).first()
        if not job or job.state not in {"queued", "running"}:
            return
        if job.state == "running" and job.lease_expires_at and job.lease_expires_at > timezone.now():
            return
        if job.attempts >= MAX_ATTEMPTS:
            job.state, job.stage, job.error_code = "failed", "failed", "WORKER_RETRY_LIMIT"
            job.save()
            return
        job.state, job.stage, job.lease_id = "running", "validating", uuid.uuid4()
        job.attempts += 1
        job.bytes_processed, job.total_bytes = 0, None
        job.lease_expires_at = timezone.now() + LEASE
        job.save()
    token = _attempt.set(Attempt(job))
    try:
        from .agent_asset_services import export_agent_trace_to_dataset, export_agent_memory_to_dataset, capture_agent_artifact_to_dataset
        if not job.requested_by or not job.requested_by.is_active:
            raise exceptions.PermissionDenied()
        request = SimpleNamespace(user=job.requested_by, tenant_id=str(job.tenant_id), project_id=job.context_project_id,
            headers={}, META={}, query_params={}, method="POST", path="/internal/dataset-imports/", auth=None)
        # Re-runs current tenancy/resource/source authorization; no frozen permission grant.
        handlers = {"trace": export_agent_trace_to_dataset, "memory": export_agent_memory_to_dataset, "artifact": capture_agent_artifact_to_dataset}
        handlers[job.kind](request=request, dataset_id=str(job.dataset_id), **job.inputs)
    except LostLease:
        pass
    except Exception as error:
        # Never persist exception text: storage errors can contain URLs/tokens or content.
        code = "SOURCE_NOT_READY" if isinstance(error, exceptions.ValidationError) else "IMPORT_FAILED"
        if isinstance(error, (exceptions.PermissionDenied, exceptions.NotFound)):
            code = "ACCESS_REVOKED_OR_SOURCE_UNAVAILABLE"
        DatasetImportJob.objects.filter(pk=job.pk, state="running", lease_id=job.lease_id).update(
            state="failed", stage="failed", error_code=code, lease_id=None, lease_expires_at=None, updated_at=timezone.now())
    finally:
        _attempt.reset(token)


def pending_jobs(limit=20):
    return list(DatasetImportJob.objects.filter(
        Q(state="queued") & (Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=timezone.now()))
        | Q(state="running", lease_expires_at__lte=timezone.now()))
        .order_by("created_at").values_list("pk", flat=True)[:limit])


@transaction.atomic
def claim_dispatch(job_id):
    """Bound broker duplicates while retaining recovery after broker data loss."""
    row = DatasetImportJob.objects.select_for_update().get(pk=job_id)
    if row.state not in {"queued", "running"} or (row.lease_expires_at and row.lease_expires_at > timezone.now()):
        return None
    row.state, row.stage = "queued", "queued"
    row.lease_id, row.lease_expires_at = uuid.uuid4(), timezone.now() + LEASE
    row.save()
    return row.lease_id
