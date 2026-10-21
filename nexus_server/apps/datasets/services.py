from __future__ import annotations

import io
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions, status

from apps.audit.services import log_audit
from apps.common.resource_catalog import (
    discoverable_resource_queryset,
    resolve_ownership_project,
)
from apps.common.models import SoftDeleteModel
from apps.common.project_scope import scope_queryset_to_current_project
from apps.tenancy.models import Project, Tenant
from apps.common.request_context import get_tenant_from_request

from .models import Dataset, DatasetFile, DatasetFileIndex, DatasetQuota, DatasetVersion
from .policy import invoke_dataset_policy
from .search_backends import get_dataset_search_backend
from .storage_backends import get_dataset_storage_backend, safe_filename


class DatasetNotFound(exceptions.APIException):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "Dataset not found."
    default_code = "NOT_FOUND"


class DatasetQuotaExceeded(exceptions.APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Dataset quota exceeded."
    default_code = "DATASET_QUOTA_EXCEEDED"


class DatasetAcquisitionPriceChanged(exceptions.APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The acquisition price changed. Review the current price and confirm again."
    default_code = "ACQUISITION_PRICE_CHANGED"


def list_datasets(*, request):
    tenant = get_tenant_from_request(request)
    return (
        discoverable_resource_queryset(
            visible_datasets(user=request.user, tenant=tenant), request=request,
            tenant=tenant, resource_type="dataset",
        )
        .select_related(*dataset_related_fields())
        .order_by("-created_at")
    )


def search_public_datasets(*, request, keyword: str):
    return invoke_dataset_policy("search_public_datasets", request=request, keyword=keyword)

def list_marketplace_datasets(*, request, apply_search=True):
    return invoke_dataset_policy("list_marketplace_datasets", request=request, apply_search=apply_search)

def get_marketplace_dataset(*, dataset_id: str) -> Dataset:
    return invoke_dataset_policy("get_marketplace_dataset", dataset_id=dataset_id)

def get_marketplace_dataset_version(*, dataset: Dataset, version: str | None = None) -> DatasetVersion:
    return invoke_dataset_policy("get_marketplace_dataset_version", dataset=dataset, version=version)

def dataset_version_snapshot_files(*, dataset: Dataset, version: DatasetVersion, file_id=None, limit=None) -> list[dict[str, Any]]:
    from .manifests import raw_entries
    from itertools import islice
    raw_files = raw_entries(version, file_id=file_id)
    if limit is not None:
        raw_files = islice(raw_files, limit)
    result: list[dict[str, Any]] = []
    for item in raw_files:
        file_id = str(item.get("file_id") or "")
        if not file_id:
            continue
        metadata = item.get("metadata_json") if isinstance(item.get("metadata_json"), dict) else {}
        result.append(
            {
                "id": file_id,
                "dataset_id": str(dataset.id),
                "file_name": str(item.get("file_name") or ""),
                "size_bytes": int(item.get("size_bytes") or 0),
                "content_type": str(item.get("content_type") or "application/octet-stream"),
                "sha256": str(item.get("sha256") or ""),
                "metadata_json": metadata,
                "status": SoftDeleteModel.STATUS_ACTIVE,
                "created_at": version.created_at,
                "updated_at": version.updated_at,
                "_object_key": str(item.get("object_key") or ""),
                "_storage_backend": str(item.get("storage_backend") or DatasetFile.STORAGE_LOCAL),
            }
        )
    return result


def search_dataset_content(*, request, keyword: str):
    tenant = get_tenant_from_request(request)
    if not keyword:
        raise exceptions.ValidationError("q is required.")
    dataset_ids = [
        str(dataset.id)
        for dataset in visible_datasets(user=request.user, tenant=tenant).select_related("tenant", "project")
        if can_read_dataset(request=request, dataset=dataset)
    ]
    return get_dataset_search_backend().search(tenant_id=str(tenant.id), dataset_ids=dataset_ids, keyword=keyword)


@transaction.atomic
def create_dataset(*, request, name: str, ownership: dict | None = None) -> Dataset:
    tenant = get_tenant_from_request(request)
    require_dataset_admin(request=request, tenant=tenant)
    Tenant.objects.select_for_update(no_key=True).get(pk=tenant.pk)
    from apps.common.resource_limits import enforce_capability

    enforce_capability(tenant=tenant, code="data.collections")
    project, _ownership_inferred = resolve_ownership_project(request=request, tenant=tenant, ownership=ownership)
    dataset = Dataset.objects.create(
        tenant=tenant,
        project=project,
        name=name,
        visibility=Dataset.VISIBILITY_PRIVATE,
        created_by=request.user,
    )
    log_write(request=request, action="datasets.create", dataset=dataset, metadata={"name": name})
    return dataset


def get_dataset(*, request, dataset_id: str) -> Dataset:
    tenant = get_tenant_from_request(request)
    dataset = discoverable_resource_queryset(
        visible_datasets(user=request.user, tenant=tenant, dataset_id=dataset_id), request=request,
        tenant=tenant, resource_type="dataset",
    ).filter(id=dataset_id).first()
    if dataset is None:
        raise DatasetNotFound()
    # Catalog discovery is intentionally broader than access to a private
    # collection. Never let the discover-only queryset become a detail, file,
    # or content-search authorization path.
    if not can_read_dataset(request=request, dataset=dataset):
        raise DatasetNotFound()
    return dataset


def can_read_dataset(*, request, dataset: Dataset) -> bool:
    return invoke_dataset_policy("can_read_dataset", request=request, dataset=dataset)

def rename_dataset(*, request, dataset_id: str, name: str) -> Dataset:
    dataset = get_mutable_dataset(request=request, dataset_id=dataset_id)
    dataset.name = name
    dataset.save(update_fields=["name", "updated_at"])
    log_write(request=request, action="datasets.rename", dataset=dataset, metadata={"name": name})
    return dataset


def set_visibility(*, request, dataset_id: str, visibility: str) -> Dataset:
    return invoke_dataset_policy("set_visibility", request=request, dataset_id=dataset_id, visibility=visibility)

def pull_dataset(*, request, dataset_id: str) -> dict[str, Any]:
    dataset = get_dataset(request=request, dataset_id=dataset_id)
    files = list(dataset.files.filter(status=SoftDeleteModel.STATUS_ACTIVE).order_by("file_name"))
    return {
        "dataset": dataset,
        "files": files,
    }


def pull_marketplace_dataset(
    *,
    request,
    dataset_id: str,
    expected_total_amount: Decimal | None = None,
    expected_currency: str = "",
    manifest_limit: int | None = None,
) -> dict[str, Any]:
    return invoke_dataset_policy("pull_marketplace_dataset", request=request, dataset_id=dataset_id, expected_total_amount=expected_total_amount, expected_currency=expected_currency, manifest_limit=manifest_limit)

def acquisition_queryset(*, request):
    return invoke_dataset_policy("acquisition_queryset", request=request)

def list_dataset_acquisitions(*, request) -> list[dict[str, Any]]:
    return invoke_dataset_policy("list_dataset_acquisitions", request=request)

def get_dataset_acquisition(*, request, usage_id: str, file_id=None, include_files=True) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
    return invoke_dataset_policy("get_dataset_acquisition", request=request, usage_id=usage_id, file_id=file_id, include_files=include_files)

def dataset_acquisition_payload(*, usage: Any) -> dict[str, Any]:
    return invoke_dataset_policy("dataset_acquisition_payload", usage=usage)

def push_dataset(*, request, dataset_id: str, uploaded_file, rights_confirmed=False) -> DatasetFile:
    from .file_scanning import import_limit, scan_asset_stream, require_scan
    dataset = get_mutable_dataset(request=request, dataset_id=dataset_id)
    if not rights_confirmed:
        raise exceptions.ValidationError({"rights_confirmed": "Confirm that you may store and share this file within this collection."})
    if uploaded_file is None or not 0 < int(uploaded_file.size) <= import_limit():
        raise exceptions.ValidationError({"file": f"Choose a non-empty file up to {import_limit()} bytes."})
    uploaded_file.seek(0)
    stats = scan_asset_stream(uploaded_file, file_name=uploaded_file.name, content_type=uploaded_file.content_type)
    require_scan(stats)
    uploaded_file.seek(0)
    uploaded_file.content_type = stats["content_type"]
    key = str(request.headers.get("Idempotency-Key", "")).strip()
    if len(key) > 128:
        raise exceptions.ValidationError("Idempotency-Key may not exceed 128 characters.")
    # Serialize a bounded single-file import with reservation/commit so a retry
    # cannot consume capacity twice. No raw input or credentials enter metadata.
    with transaction.atomic():
        Tenant.objects.select_for_update(no_key=True).get(pk=dataset.tenant_id)
        if key:
            previous = dataset.files.filter(status="active", uploaded_by=request.user, metadata_json__import_key=key).first()
            if previous:
                if previous.sha256 != stats["sha256"] or previous.file_name != safe_filename(uploaded_file.name):
                    raise exceptions.ValidationError("This import key was already used for another file.")
                return previous
        result = create_dataset_file_from_upload(dataset=dataset, uploaded_file=uploaded_file,
            uploaded_by=request.user, expected_sha256=stats["sha256"], metadata={
                "source_type": "user_upload", "scan_status": "passed", "policy_status": "approved",
                "scan_metadata": stats, "license_status": "internal", "consent_status": "approved",
                "sensitivity_level": "internal", "import_key": key,
            })
        log_write(request=request, action="datasets.file.import", dataset=dataset,
            metadata={"file_id": str(result.id), "content_type": result.content_type, "size_bytes": result.size_bytes})
        return result


def list_versions(*, request, dataset_id: str):
    dataset = get_dataset(request=request, dataset_id=dataset_id)
    return dataset.versions.order_by("-created_at")


@transaction.atomic
def create_version(*, request, dataset_id: str, release_notes: str = "") -> DatasetVersion:
    dataset = get_mutable_dataset(request=request, dataset_id=dataset_id)
    Tenant.objects.select_for_update(no_key=True).get(pk=dataset.tenant_id)
    dataset = Dataset.objects.select_for_update(no_key=True).get(pk=dataset.pk, status=SoftDeleteModel.STATUS_ACTIVE)
    next_number = dataset.versions.count() + 1
    from django.db.models import Sum, Count
    from .manifests import store_entries, release_summary
    files = dataset.files.filter(status=SoftDeleteModel.STATUS_ACTIVE).order_by("file_name")
    totals = files.aggregate(count=Count("pk"), size=Sum("size_bytes"))
    version = DatasetVersion.objects.create(
        tenant=dataset.tenant,
        project=dataset.project,
        dataset=dataset,
        version=f"v{next_number}",
        file_count=totals["count"],
        size_bytes=totals["size"] or 0,
        snapshot_json={
            "release_notes": str(release_notes or "").strip(),
            "format": 2,
        },
        created_by=request.user,
    )
    store_entries(version, (
                {
                    "file_id": str(file.id),
                    "file_name": file.file_name,
                    "size_bytes": file.size_bytes,
                    "content_type": file.content_type,
                    "object_key": file.object_key,
                    "sha256": file.sha256,
                    "storage_backend": file.storage_backend,
                    "metadata_json": version_file_metadata(file.metadata_json),
                }
                for file in files.iterator(chunk_size=100)
    ))
    version.snapshot_json["summary"] = release_summary(version)
    version.save(update_fields=["snapshot_json", "updated_at"])
    dataset.current_version = version.version
    dataset.save(update_fields=["current_version", "updated_at"])
    log_write(request=request, action="datasets.version.create", dataset=dataset, metadata={"version": version.version})
    return version


@transaction.atomic
def delete_dataset(*, request, dataset_id: str) -> Dataset:
    dataset = get_mutable_dataset(request=request, dataset_id=dataset_id)
    Tenant.objects.select_for_update(no_key=True).get(pk=dataset.tenant_id)
    dataset = Dataset.objects.select_for_update(no_key=True).get(pk=dataset.pk)
    if dataset.visibility == Dataset.VISIBILITY_PUBLIC:
        raise exceptions.ValidationError("Unpublish this collection before deleting it.")
    if dataset.import_jobs.filter(state__in=["queued", "running"]).exists():
        raise exceptions.ValidationError("Cancel or finish active imports before deleting this collection.")
    dataset.delete()
    log_write(request=request, action="datasets.delete", dataset=dataset)
    return dataset


def set_pricing(
    *,
    request,
    dataset_id: str,
    plan_id: str = "",
    pricing_type: str | None = None,
    fixed_price: Decimal | None = None,
    price_per_gb: Decimal | None = None,
    currency: str = "USD",
) -> Any:
    return invoke_dataset_policy("set_pricing", request=request, dataset_id=dataset_id, plan_id=plan_id, pricing_type=pricing_type, fixed_price=fixed_price, price_per_gb=price_per_gb, currency=currency)

@transaction.atomic
def set_quota(*, request, dataset_id: str, max_size: str) -> DatasetQuota:
    dataset = get_mutable_dataset(request=request, dataset_id=dataset_id)
    Tenant.objects.select_for_update(no_key=True).get(pk=dataset.tenant_id)
    dataset = Dataset.objects.select_for_update(no_key=True).get(pk=dataset.pk, status=SoftDeleteModel.STATUS_ACTIVE)
    from .transfers import pending
    from django.db.models import Sum
    reserved = pending(dataset.tenant_id, "write").filter(dataset=dataset).aggregate(total=Sum("size_bytes"))["total"] or 0
    max_size_bytes = parse_size(max_size)
    if dataset.size_bytes + reserved > max_size_bytes:
        raise DatasetQuotaExceeded("Existing files and pending imports exceed the requested quota.")
    quota, _ = DatasetQuota.objects.update_or_create(
        dataset=dataset,
        defaults={"tenant": dataset.tenant, "max_size_bytes": max_size_bytes, "raw_value": max_size},
    )
    log_write(
        request=request,
        action="datasets.quota.set",
        dataset=dataset,
        metadata={"max_size": max_size, "max_size_bytes": max_size_bytes},
    )
    return quota


def visible_datasets(*, user, tenant: Tenant, dataset_id=None):
    return invoke_dataset_policy("visible_datasets", user=user, tenant=tenant, dataset_id=dataset_id)

def validate_dataset_publication(*, dataset: Dataset) -> None:
    return invoke_dataset_policy("validate_dataset_publication", dataset=dataset)

def get_mutable_dataset(*, request, dataset_id: str) -> Dataset:
    dataset = get_dataset(request=request, dataset_id=dataset_id)
    if not can_manage_dataset(user=request.user, dataset=dataset):
        raise exceptions.PermissionDenied("Dataset admin permission is required.")
    return dataset


def can_manage_dataset(*, user, dataset: Dataset) -> bool:
    return invoke_dataset_policy("can_manage_dataset", user=user, dataset=dataset)

def require_dataset_admin(*, request, tenant: Tenant) -> None:
    return invoke_dataset_policy("require_dataset_admin", request=request, tenant=tenant)

def resolve_project_from_request(*, request, tenant: Tenant) -> Project | None:
    project_id = getattr(request, "project_id", "")
    if not project_id:
        return None
    return Project.objects.filter(tenant=tenant, id=project_id, status=SoftDeleteModel.STATUS_ACTIVE).first()


def enforce_quota(*, dataset: Dataset, incoming_size: int) -> None:
    quota = getattr(dataset, "quota", None)
    if quota is None:
        return
    if dataset.size_bytes + incoming_size > quota.max_size_bytes:
        raise DatasetQuotaExceeded()


def create_dataset_file_from_upload(*, dataset: Dataset, uploaded_file, uploaded_by, metadata: dict[str, Any] | None = None, expected_sha256: str | None = None) -> DatasetFile:
    size = int(getattr(uploaded_file, "size", 0) or 0)
    from .transfers import reserve_write, ReservedUpload, discard_write
    from .models import DatasetTransfer
    if size < 0:
        raise exceptions.ValidationError("Invalid file size.")
    transfer = reserve_write(dataset, size, getattr(uploaded_file, "name", "dataset-file"))
    try:
        stored = save_uploaded_file(dataset=dataset, uploaded_file=ReservedUpload(uploaded_file, transfer, expected_sha256))
        with transaction.atomic():
            Tenant.objects.select_for_update(no_key=True).get(pk=dataset.tenant_id)
            current = Dataset.objects.select_for_update(no_key=True).get(pk=dataset.pk, status=SoftDeleteModel.STATUS_ACTIVE)
            lease = DatasetTransfer.objects.select_for_update().get(pk=transfer.pk)
            if lease.state != "active" or lease.expires_at <= timezone.now():
                raise exceptions.ValidationError("Storage reservation expired. Retry the import.")
            # A plan/quota may have changed while bytes were copied. Never commit
            # beyond the current limits, even when the original reservation fit.
            from apps.common.resource_limits import enforce_capability
            enforce_capability(tenant=current.tenant, code="data.files")
            enforce_capability(tenant=current.tenant, code="data.storage_gb", requested=Decimal(stored.size_bytes) / Decimal(1024 ** 3))
            enforce_quota(dataset=current, incoming_size=stored.size_bytes)
            dataset_file = DatasetFile.objects.create(
                tenant=current.tenant, project=current.project, dataset=current,
                file_name=safe_filename(getattr(uploaded_file, "name", "dataset-file")),
                storage_path=stored.storage_path, object_key=stored.object_key,
                storage_backend=stored.backend, sha256=stored.sha256, size_bytes=stored.size_bytes,
                content_type=getattr(uploaded_file, "content_type", "") or "",
                metadata_json=metadata or {}, uploaded_by=uploaded_by,
            )
            current.size_bytes += stored.size_bytes
            current.file_count += 1
            current.save(update_fields=["size_bytes", "file_count", "updated_at"])
            lease.state, lease.file = "completed", dataset_file
            lease.save(update_fields=["state", "file"])
            from .import_jobs import commit_import
            commit_import(dataset_file)
        dataset.size_bytes, dataset.file_count = current.size_bytes, current.file_count
    except BaseException:
        transfer.refresh_from_db()
        if transfer.state != "completed":
            try:
                discard_write(transfer)
            except Exception:
                DatasetTransfer.objects.filter(pk=transfer.pk).update(state="cleaning", expires_at=timezone.now())
        raise
    # Indexing is a separate operation; an index outage must not duplicate a saved asset on retry.
    try:
        index_dataset_file(dataset_file=dataset_file)
    except Exception:
        import logging
        logging.getLogger(__name__).warning("Dataset indexing failed for saved file %s", dataset_file.id)
    return dataset_file


def create_dataset_file_from_bytes(
    *,
    dataset: Dataset,
    file_name: str,
    content: bytes,
    content_type: str,
    uploaded_by,
    metadata: dict[str, Any] | None = None,
) -> DatasetFile:
    uploaded_file = InMemoryDatasetUpload(file_name=file_name, content=content, content_type=content_type)
    return create_dataset_file_from_upload(
        dataset=dataset,
        uploaded_file=uploaded_file,
        uploaded_by=uploaded_by,
        metadata=metadata,
    )


class InMemoryDatasetUpload:
    def __init__(self, *, file_name: str, content: bytes, content_type: str) -> None:
        self.name = file_name
        self.content_type = content_type
        self._content = bytes(content)
        self.size = len(self._content)

    def chunks(self, chunk_size: int = 64 * 1024):
        stream = io.BytesIO(self._content)
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            yield chunk


def version_file_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    allowed_keys = {
        "source_type",
        "agent_id",
        "run_id",
        "runtime_id",
        "event_id",
        "artifact_id",
        "memory_item_ids",
        "workspace_path",
        "original_file_name",
        "export_format",
        "redaction_status",
        "redaction_metadata",
        "scan_status",
        "policy_status",
        "scan_metadata",
        "consent_status",
        "license_status",
        "sensitivity_level",
    }
    return {key: metadata.get(key) for key in allowed_keys if key in metadata}


def validate_dataset_file_publication(*, dataset_file: DatasetFile) -> None:
    return invoke_dataset_policy("validate_dataset_file_publication", dataset_file=dataset_file)


def effective_dataset_pricing(*, pricing: Any) -> dict[str, Any]:
    return invoke_dataset_policy("effective_dataset_pricing", pricing=pricing)

def calculate_dataset_pull_cost(*, dataset: Dataset, bytes_transferred: int | None = None) -> Decimal:
    return invoke_dataset_policy("calculate_dataset_pull_cost", dataset=dataset, bytes_transferred=bytes_transferred)

def dataset_pull_currency(*, dataset: Dataset) -> str:
    return invoke_dataset_policy("dataset_pull_currency", dataset=dataset)


def dataset_related_fields():
    return invoke_dataset_policy("dataset_related_fields")


def dataset_retention_status(*, dataset: Dataset) -> dict:
    return invoke_dataset_policy("dataset_retention_status", dataset=dataset)

def save_uploaded_file(*, dataset: Dataset, uploaded_file):
    backend = get_dataset_storage_backend()
    return backend.save(
        tenant=dataset.tenant,
        dataset_id=str(dataset.id),
        file_name=str(getattr(uploaded_file, "name", "dataset-file")),
        uploaded_file=uploaded_file,
    )


def get_dataset_file(*, request, dataset_id: str, file_id: str) -> DatasetFile:
    dataset = get_dataset(request=request, dataset_id=dataset_id)
    dataset_file = dataset.files.filter(id=file_id, status=SoftDeleteModel.STATUS_ACTIVE).first()
    if dataset_file is None:
        raise DatasetNotFound("Dataset file not found.")
    return dataset_file


def download_dataset_file(*, request, dataset_id: str, file_id: str):
    dataset_file = get_dataset_file(request=request, dataset_id=dataset_id, file_id=file_id)
    return open_dataset_file_response(request=request, dataset_file=dataset_file, action="datasets.file.download")


def download_marketplace_dataset_file(*, request, dataset_id: str, file_id: str):
    return invoke_dataset_policy("download_marketplace_dataset_file", request=request, dataset_id=dataset_id, file_id=file_id)

def download_dataset_acquisition_file(*, request, usage_id: str, file_id: str):
    return invoke_dataset_policy("download_dataset_acquisition_file", request=request, usage_id=usage_id, file_id=file_id)

def has_marketplace_dataset_entitlement(*, tenant: Tenant, dataset: Dataset) -> bool:
    return invoke_dataset_policy("has_marketplace_dataset_entitlement", tenant=tenant, dataset=dataset)

def open_dataset_file_response(*, request, dataset_file: DatasetFile, action: str):
    from .downloads import file_response
    return file_response(request=request, dataset=dataset_file.dataset,
        object_key=dataset_file.object_key or dataset_file.storage_path,
        storage_backend=dataset_file.storage_backend, file_name=dataset_file.file_name,
        size=dataset_file.size_bytes, sha256=dataset_file.sha256,
        content_type=dataset_file.content_type or "application/octet-stream", action=action, file_id=dataset_file.id)


def open_dataset_snapshot_file_response(*, request, dataset: Dataset, snapshot_file: dict[str, Any], action: str):
    object_key = str(snapshot_file.get("_object_key") or "")
    if not object_key:
        raise DatasetNotFound("Dataset file content not found.")
    from .downloads import file_response
    return file_response(request=request, dataset=dataset, object_key=object_key,
        storage_backend=str(snapshot_file.get("_storage_backend") or DatasetFile.STORAGE_LOCAL),
        file_name=str(snapshot_file.get("file_name") or "download"),
        size=int(snapshot_file.get("size_bytes") or 0), sha256=str(snapshot_file.get("sha256") or ""),
        content_type=str(snapshot_file.get("content_type") or "application/octet-stream"),
        action=action, file_id=snapshot_file["id"])


def download_dataset_version_file(*, request, dataset_id: str, version_id: str, file_id: str):
    dataset = get_dataset(request=request, dataset_id=dataset_id)
    version = dataset.versions.filter(id=version_id, status=SoftDeleteModel.STATUS_ACTIVE).first()
    if version is None:
        raise DatasetNotFound("Dataset version not found.")
    snapshot_file = next((item for item in dataset_version_snapshot_files(dataset=dataset, version=version, file_id=file_id)
        if item["id"] == str(file_id)), None)
    if snapshot_file is None:
        raise DatasetNotFound("Dataset file not found in version.")
    return open_dataset_snapshot_file_response(request=request, dataset=dataset, snapshot_file=snapshot_file,
        action="datasets.version_file.download")


def index_dataset_file(*, dataset_file: DatasetFile) -> DatasetFileIndex:
    index, _ = DatasetFileIndex.objects.update_or_create(
        file=dataset_file,
        defaults={
            "tenant": dataset_file.tenant,
            "project": dataset_file.project,
            "dataset": dataset_file.dataset,
            "version": dataset_file.dataset.current_version,
            "index_status": DatasetFileIndex.STATUS_PENDING,
            "error_code": "",
        },
    )
    if not getattr(settings, "NEXUS_DATASET_INDEX_SYNC", True):
        return index
    if not is_indexable(dataset_file):
        index.index_status = DatasetFileIndex.STATUS_FAILED
        index.error_code = "UNSUPPORTED_CONTENT_TYPE"
        index.indexed_at = timezone.now()
        index.save(update_fields=["index_status", "error_code", "indexed_at", "updated_at"])
        return index
    if dataset_file.size_bytes > int(getattr(settings, "NEXUS_DATASET_INDEX_MAX_BYTES", 1_048_576)):
        index.index_status = DatasetFileIndex.STATUS_FAILED
        index.error_code = "FILE_TOO_LARGE"
        index.indexed_at = timezone.now()
        index.save(update_fields=["index_status", "error_code", "indexed_at", "updated_at"])
        return index
    stream = None
    try:
        backend = get_dataset_storage_backend(dataset_file.storage_backend)
        stream = backend.open(object_key=dataset_file.object_key or dataset_file.storage_path)
        raw = stream.read()
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        index.index_status = DatasetFileIndex.STATUS_FAILED
        index.error_code = "DECODE_FAILED"
        index.indexed_at = timezone.now()
        index.save(update_fields=["index_status", "error_code", "indexed_at", "updated_at"])
        return index
    except Exception:
        index.index_status = DatasetFileIndex.STATUS_FAILED
        index.error_code = "READ_FAILED"
        index.indexed_at = timezone.now()
        index.save(update_fields=["index_status", "error_code", "indexed_at", "updated_at"])
        return index
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()
    index.content_text = text
    index.content_sha256 = dataset_file.sha256
    index.index_status = DatasetFileIndex.STATUS_INDEXED
    index.error_code = ""
    index.indexed_at = timezone.now()
    index.save(update_fields=["content_text", "content_sha256", "index_status", "error_code", "indexed_at", "updated_at"])
    get_dataset_search_backend().index_file(dataset_file=dataset_file, index=index)
    return index


def is_indexable(dataset_file: DatasetFile) -> bool:
    content_type = (dataset_file.content_type or "").split(";", 1)[0].strip().lower()
    if content_type in {"text/plain", "text/markdown", "application/json", "application/jsonl", "application/x-ndjson", "text/csv"}:
        return True
    suffix = Path(dataset_file.file_name).suffix.lower()
    return suffix in {".txt", ".md", ".markdown", ".json", ".jsonl", ".ndjson", ".csv"}


def parse_size(value: str) -> int:
    text = str(value).strip().upper()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(B|KB|MB|GB)?", text)
    if not match:
        raise exceptions.ValidationError("max_size must be bytes or a value like 500MB or 1GB.")
    number = float(match.group(1))
    unit = match.group(2) or "B"
    multiplier = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}[unit]
    return int(number * multiplier)


def log_write(*, request, action: str, dataset: Dataset, metadata: dict[str, Any] | None = None) -> None:
    log_audit(
        request=request,
        action=action,
        actor=request.user,
        resource_type="dataset",
        resource_id=dataset.pk,
        metadata=metadata or {},
    )
