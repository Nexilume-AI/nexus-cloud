"""Content-safe operational summaries and conservative retention decisions."""
from datetime import timedelta
import hashlib
import logging
import shutil
import tempfile

from django.db.models import Count, Sum, Q, Min
from django.utils import timezone

from .models import DatasetFile, DatasetImportJob, DatasetTransfer
from .storage_backends import get_dataset_storage_backend


def operational_metrics(tenant, project_id=""):
    now = timezone.now()
    jobs = DatasetImportJob.objects.filter(tenant=tenant)
    if project_id:
        jobs = jobs.filter(dataset__project_id=project_id)
    values = jobs.aggregate(
        queued=Count("pk", filter=Q(state="queued")),
        running=Count("pk", filter=Q(state="running")),
        failed_24h=Count("pk", filter=Q(state="failed", updated_at__gte=now - timedelta(days=1))),
        completed_24h=Count("pk", filter=Q(state="completed", updated_at__gte=now - timedelta(days=1))),
        stalled=Count("pk", filter=Q(state="running", lease_expires_at__lte=now)),
        oldest_queued_at=Min("created_at", filter=Q(state="queued")),
    )
    oldest = values.pop("oldest_queued_at")
    values["queue_age_seconds"] = int((now - oldest).total_seconds()) if oldest else 0
    transfers = DatasetTransfer.objects.filter(tenant=tenant)
    if project_id:
        transfers = transfers.filter(dataset__project_id=project_id)
    values.update(transfers.aggregate(
        cleanup_backlog=Count("pk", filter=Q(kind="write", state__in=["active", "cleaning"], expires_at__lte=now)),
        reserved_write_bytes=Sum("size_bytes", filter=Q(kind="write", state="active", expires_at__gt=now)),
        completed_downloads_24h=Count("pk", filter=Q(kind="export", state="completed", created_at__gte=now - timedelta(days=1))),
        expired_downloads_24h=Count("pk", filter=Q(kind="export", state="active", expires_at__lte=now, created_at__gte=now - timedelta(days=1))),
    ))
    values["reserved_write_bytes"] = values["reserved_write_bytes"] or 0
    # Capacity only; never expose server paths. Each worker's host must also be
    # monitored by infrastructure monitoring when workers use separate disks.
    values["spool_free_bytes"] = shutil.disk_usage(tempfile.gettempdir()).free
    return values


def ensure_operational_alerts(tenant):
    from .policy import invoke_dataset_policy
    return invoke_dataset_policy("ensure_operational_alerts", tenant=tenant)


def maintain_data_assets():
    from .policy import invoke_dataset_policy
    return invoke_dataset_policy("maintain_data_assets")


def retention_status(dataset):
    from .services import dataset_retention_status
    return dataset_retention_status(dataset=dataset)


def verify_object(*, backend, key, expected_size, expected_hash):
    """Streaming integrity check for restore drills; no content/path in results."""
    size, digest = 0, hashlib.sha256()
    try:
        with get_dataset_storage_backend(backend).open(object_key=key) as stream:
            while True:
                chunk = stream.read(256 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
        if size != expected_size or (expected_hash and digest.hexdigest() != expected_hash):
            return "INTEGRITY_MISMATCH"
        return "VERIFIED" if expected_hash else "HASH_UNAVAILABLE"
    except Exception:
        return "OBJECT_UNAVAILABLE"
