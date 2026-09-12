"""Short database locks around reservations; storage I/O never holds a tenant lock."""
from __future__ import annotations

import hashlib
import time
import uuid
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import exceptions

from apps.common.resource_limits import enforce_capability, record_capability_usage
from apps.common.models import SoftDeleteModel
from apps.tenancy.models import Tenant
from .models import Dataset, DatasetTransfer
from .storage_backends import get_dataset_storage_backend, safe_filename

GB = Decimal(1024 ** 3)
LEASE = timedelta(minutes=15)


def pending(tenant_id, kind):
    return DatasetTransfer.objects.filter(tenant_id=tenant_id, kind=kind, state="active", expires_at__gt=timezone.now())


@transaction.atomic
def reserve_write(dataset, size, name):
    # NO KEY UPDATE still serializes capacity writers, but permits FK KEY SHARE
    # checks from independent indexing/audit inserts. Full UPDATE can deadlock
    # with those inserts when PostgreSQL checks deferred FKs at commit.
    Tenant.objects.select_for_update(no_key=True).get(pk=dataset.tenant_id)
    current = Dataset.objects.select_for_update(no_key=True).get(pk=dataset.pk, status=SoftDeleteModel.STATUS_ACTIVE)
    reservations = pending(dataset.tenant_id, "write")
    reserved = reservations.aggregate(total=Sum("size_bytes"))["total"] or 0
    enforce_capability(tenant=current.tenant, code="data.files", requested=1 + reservations.count())
    enforce_capability(tenant=current.tenant, code="data.storage_gb", requested=Decimal(size + reserved) / GB)
    from .services import enforce_quota
    collection_reserved = reservations.filter(dataset=current).aggregate(total=Sum("size_bytes"))["total"] or 0
    enforce_quota(dataset=current, incoming_size=size + collection_reserved)
    backend = get_dataset_storage_backend()
    return DatasetTransfer.objects.create(
        tenant=current.tenant, dataset=current, kind="write", size_bytes=size,
        identity=uuid.uuid4().hex, expires_at=timezone.now() + LEASE,
        storage_backend=backend.name,
        object_key=f"{current.tenant_id}/{current.id}/{uuid.uuid4().hex}_{safe_filename(name)}",
    )


def renew(transfer):
    changed = DatasetTransfer.objects.filter(pk=transfer.pk, state="active", expires_at__gt=timezone.now()).update(
        expires_at=timezone.now() + LEASE,
    )
    if not changed:
        # Another range may already have completed this logical download.
        if transfer.kind == "export" and DatasetTransfer.objects.filter(pk=transfer.pk, state="completed").exists():
            return
        raise exceptions.ValidationError("Transfer reservation expired. Start the transfer again.")


class ReservedUpload:
    def __init__(self, upload, transfer, expected_sha256=None):
        self.upload, self.transfer = upload, transfer
        self.name = upload.name
        self.content_type = getattr(upload, "content_type", "")
        self.size = transfer.size_bytes
        self.object_key = transfer.object_key
        self.expected_sha256 = expected_sha256

    def multipart_started(self, upload_id):
        DatasetTransfer.objects.filter(pk=self.transfer.pk, state="active").update(multipart_id=upload_id)

    def chunks(self):
        size = 0
        digest = hashlib.sha256()
        last = time.monotonic()
        for chunk in self.upload.chunks():
            size += len(chunk)
            if size > self.size:
                raise exceptions.ValidationError("File exceeds its reserved size.")
            digest.update(chunk)
            if time.monotonic() - last >= 30:
                renew(self.transfer)
                last = time.monotonic()
            yield chunk
        if size != self.size:
            raise exceptions.ValidationError("Incomplete file: size does not match the reserved size.")
        if self.expected_sha256 and digest.hexdigest() != self.expected_sha256:
            raise exceptions.ValidationError("Agent output artifact changed after scan. Scan it again before capture.")
        renew(self.transfer)


def discard_write(transfer):
    with transaction.atomic():
        transfer = DatasetTransfer.objects.select_for_update().get(pk=transfer.pk)
        if transfer.state == "completed" or transfer.file_id is not None:
            return
        transfer.state = "cleaning"
        transfer.save(update_fields=["state"])
    backend = get_dataset_storage_backend(transfer.storage_backend)
    if transfer.multipart_id and backend.name == "s3":
        from django.conf import settings
        try:
            backend.client().abort_multipart_upload(Bucket=settings.NEXUS_DATASET_S3_BUCKET,
                Key=transfer.object_key, UploadId=transfer.multipart_id)
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code != "NoSuchUpload":
                raise
    backend.delete(object_key=transfer.object_key)
    if backend.name == "local":
        backend.delete(object_key=transfer.object_key + ".part")
    DatasetTransfer.objects.filter(pk=transfer.pk).exclude(state="completed").update(state="failed")


@transaction.atomic
def reserve_export(dataset, size, identity):
    Tenant.objects.select_for_update(no_key=True).get(pk=dataset.tenant_id)
    existing = DatasetTransfer.objects.filter(identity=identity).first()
    if existing and existing.created_at < timezone.now() - timedelta(hours=24):
        raise exceptions.ValidationError("Download session expired. Start a new download.")
    if existing and (existing.state == "completed" or (existing.state == "active" and existing.expires_at > timezone.now())):
        return existing
    reserved = pending(dataset.tenant_id, "export").aggregate(total=Sum("size_bytes"))["total"] or 0
    enforce_capability(tenant=dataset.tenant, code="data.export_gb_per_30_days", requested=Decimal(size + reserved) / GB)
    if existing:
        existing.state, existing.expires_at = "active", timezone.now() + LEASE
        existing.save(update_fields=["state", "expires_at"])
        return existing
    return DatasetTransfer.objects.create(tenant=dataset.tenant, dataset=dataset, kind="export", size_bytes=size,
        identity=identity, expires_at=timezone.now() + LEASE)


@transaction.atomic
def complete_export(transfer):
    Tenant.objects.select_for_update(no_key=True).get(pk=transfer.tenant_id)
    current = DatasetTransfer.objects.select_for_update().get(pk=transfer.pk)
    if current.state == "completed":
        return
    if current.state != "active" or current.expires_at <= timezone.now():
        raise exceptions.ValidationError("Download reservation expired.")
    record_capability_usage(tenant=current.tenant, code="data.export_gb_per_30_days",
        amount=Decimal(current.size_bytes) / GB, idempotency_key=f"dataset-export:{current.identity}",
        metadata={"dataset_id": str(current.dataset_id)})
    current.state = "completed"
    current.save(update_fields=["state"])


def cleanup_expired_writes(limit=100, *, queryset=None):
    """Claim before deleting. A late writer cannot commit a claimed reservation."""
    rows = DatasetTransfer.objects if queryset is None else queryset
    if getattr(rows, "model", None) is not DatasetTransfer:
        raise ValueError("Dataset cleanup requires transfer rows.")
    ids = list(rows.filter(kind="write", state__in=["active", "cleaning"],
        expires_at__lte=timezone.now()).values_list("id", flat=True)[:limit])
    cleaned = 0
    for transfer_id in ids:
        with transaction.atomic():
            row = rows.select_for_update().filter(pk=transfer_id).first()
            if row is None:
                continue
            if row.state not in {"active", "cleaning"} or row.expires_at > timezone.now():
                continue
            row.state = "cleaning"
            row.save(update_fields=["state"])
        try:
            discard_write(row)
            cleaned += 1
        except Exception:
            # Retain durable cleanup intent for the next worker attempt.
            import logging
            logging.getLogger(__name__).warning("Dataset cleanup deferred: transfer=%s", transfer_id)
            continue
    return cleaned
