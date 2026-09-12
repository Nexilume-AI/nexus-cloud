from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from django.conf import settings
from rest_framework import exceptions

from apps.tenancy.models import Tenant


@dataclass(frozen=True)
class StoredObject:
    backend: str
    object_key: str
    storage_path: str
    size_bytes: int
    sha256: str


class DatasetStorageBackend:
    name = ""

    def save(self, *, tenant: Tenant, dataset_id: str, file_name: str, uploaded_file) -> StoredObject:
        raise NotImplementedError

    def open(self, *, object_key: str):
        raise NotImplementedError

    def exists(self, *, object_key: str) -> bool:
        raise NotImplementedError

    def delete(self, *, object_key: str) -> None:
        raise NotImplementedError

    def open_range(self, *, object_key: str, start: int, length: int):
        stream = self.open(object_key=object_key)
        try:
            stream.seek(start)
            return stream
        except BaseException:
            stream.close()
            raise


class LocalDatasetStorageBackend(DatasetStorageBackend):
    name = "local"

    def save(self, *, tenant: Tenant, dataset_id: str, file_name: str, uploaded_file) -> StoredObject:
        root = dataset_storage_root()
        target_dir = root / str(tenant.id) / str(dataset_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex}_{safe_filename(file_name)}"
        target = resolve_local_object_key(getattr(uploaded_file, "object_key", "")) if getattr(uploaded_file, "object_key", "") else target_dir / filename
        digest = hashlib.sha256()
        size = 0
        temporary = target.with_name(target.name + ".part")
        try:
            with temporary.open("xb") as handle:
                for chunk in uploaded_file.chunks():
                    size += len(chunk)
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        object_key = target.relative_to(root).as_posix()
        return StoredObject(
            backend=self.name,
            object_key=object_key,
            storage_path=object_key,
            size_bytes=size,
            sha256=digest.hexdigest(),
        )

    def open(self, *, object_key: str) -> BinaryIO:
        path = resolve_local_object_key(object_key)
        if not path.exists() or not path.is_file():
            raise exceptions.NotFound("Dataset file content not found.")
        return path.open("rb")

    def exists(self, *, object_key: str) -> bool:
        path = resolve_local_object_key(object_key)
        return path.exists() and path.is_file()

    def delete(self, *, object_key: str) -> None:
        resolve_local_object_key(object_key).unlink(missing_ok=True)


class S3CompatibleDatasetStorageBackend(DatasetStorageBackend):
    name = "s3"

    def __init__(self) -> None:
        if not settings.NEXUS_DATASET_S3_BUCKET:
            raise exceptions.APIException("Dataset S3 bucket is not configured.")

    def save(self, *, tenant: Tenant, dataset_id: str, file_name: str, uploaded_file) -> StoredObject:
        object_key = getattr(uploaded_file, "object_key", "") or f"{tenant.id}/{dataset_id}/{uuid.uuid4().hex}_{safe_filename(file_name)}"
        digest = hashlib.sha256()
        # Keep at most one S3 part in memory, never the complete object.
        part_size = 8 * 1024 * 1024
        buffer = bytearray()
        parts = []
        upload_id = None
        client = self.client()
        kwargs = {"Bucket": settings.NEXUS_DATASET_S3_BUCKET, "Key": object_key}
        content_type = str(getattr(uploaded_file, "content_type", "") or "application/octet-stream")
        size = 0
        try:
            for chunk in uploaded_file.chunks():
                size += len(chunk)
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    take = min(part_size - len(buffer), len(view))
                    buffer.extend(view[:take])
                    view = view[take:]
                    if len(buffer) == part_size:
                        if upload_id is None:
                            upload_id = client.create_multipart_upload(**kwargs, ContentType=content_type)["UploadId"]
                            if hasattr(uploaded_file, "multipart_started"):
                                uploaded_file.multipart_started(upload_id)
                        if len(parts) >= 10000:
                            raise exceptions.ValidationError("Dataset exceeds the multipart part limit.")
                        number = len(parts) + 1
                        result = client.upload_part(**kwargs, UploadId=upload_id, PartNumber=number, Body=bytes(buffer))
                        parts.append({"PartNumber": number, "ETag": result["ETag"]})
                        buffer.clear()
            if upload_id is None:
                client.put_object(**kwargs, Body=bytes(buffer), ContentType=content_type)
            else:
                if buffer:
                    if len(parts) >= 10000:
                        raise exceptions.ValidationError("Dataset exceeds the multipart part limit.")
                    number = len(parts) + 1
                    result = client.upload_part(**kwargs, UploadId=upload_id, PartNumber=number, Body=bytes(buffer))
                    parts.append({"PartNumber": number, "ETag": result["ETag"]})
                client.complete_multipart_upload(**kwargs, UploadId=upload_id, MultipartUpload={"Parts": parts})
        except BaseException:
            if upload_id is not None:
                try:
                    client.abort_multipart_upload(**kwargs, UploadId=upload_id)
                except Exception:
                    # Bucket lifecycle must also abort abandoned multipart uploads.
                    pass
            raise
        return StoredObject(
            backend=self.name,
            object_key=object_key,
            storage_path=object_key,
            size_bytes=size,
            sha256=digest.hexdigest(),
        )

    def open(self, *, object_key: str):
        try:
            result = self.client().get_object(Bucket=settings.NEXUS_DATASET_S3_BUCKET, Key=object_key)
        except Exception as exc:  # noqa: BLE001 - normalized at storage boundary.
            raise exceptions.NotFound("Dataset file content not found.") from exc
        return result["Body"]

    def exists(self, *, object_key: str) -> bool:
        try:
            self.client().head_object(Bucket=settings.NEXUS_DATASET_S3_BUCKET, Key=object_key)
            return True
        except Exception:
            return False

    def open_range(self, *, object_key: str, start: int, length: int):
        if length == 0:
            return self.open(object_key=object_key)
        try:
            return self.client().get_object(
                Bucket=settings.NEXUS_DATASET_S3_BUCKET, Key=object_key,
                Range=f"bytes={start}-{start + length - 1}",
            )["Body"]
        except Exception as exc:
            raise exceptions.NotFound("Dataset file content not found.") from exc

    def delete(self, *, object_key: str) -> None:
        self.client().delete_object(Bucket=settings.NEXUS_DATASET_S3_BUCKET, Key=object_key)

    def client(self):
        import boto3

        kwargs = {
            "service_name": "s3",
            "region_name": settings.NEXUS_DATASET_S3_REGION or None,
            "endpoint_url": settings.NEXUS_DATASET_S3_ENDPOINT_URL or None,
        }
        if settings.NEXUS_DATASET_S3_ACCESS_KEY_ID:
            kwargs["aws_access_key_id"] = settings.NEXUS_DATASET_S3_ACCESS_KEY_ID
        if settings.NEXUS_DATASET_S3_SECRET_ACCESS_KEY:
            kwargs["aws_secret_access_key"] = settings.NEXUS_DATASET_S3_SECRET_ACCESS_KEY
        return boto3.client(**kwargs)


def get_dataset_storage_backend(name: str | None = None) -> DatasetStorageBackend:
    backend_name = (name or settings.NEXUS_DATASET_STORAGE_BACKEND or "local").lower()
    if backend_name == "local":
        return LocalDatasetStorageBackend()
    if backend_name in {"s3", "s3_compatible", "s3-compatible"}:
        return S3CompatibleDatasetStorageBackend()
    raise exceptions.ValidationError("Unsupported dataset storage backend.")


def dataset_storage_root() -> Path:
    return Path(settings.NEXUS_DATASET_STORAGE_ROOT).resolve()


def resolve_local_object_key(object_key: str) -> Path:
    root = dataset_storage_root()
    path = (root / object_key).resolve()
    if root not in path.parents and path != root:
        raise exceptions.ValidationError("Invalid dataset storage path.")
    return path


def safe_filename(name: str) -> str:
    value = Path(str(name)).name.strip().replace("\\", "_").replace("/", "_")
    return value or "dataset-file"
