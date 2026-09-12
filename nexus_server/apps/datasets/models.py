from __future__ import annotations

import uuid
from django.conf import settings
from django.db import models

from apps.common.models import SoftDeleteModel
from apps.tenancy.models import Project, Tenant


class DatasetImportJob(models.Model):
    """Database-backed work queue. Inputs contain IDs only, never credentials/content."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    dataset = models.ForeignKey("Dataset", on_delete=models.CASCADE, related_name="import_jobs")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    context_project_id = models.CharField(max_length=36, blank=True)
    request_key = models.CharField(max_length=128)
    request_hash = models.CharField(max_length=64)
    kind = models.CharField(max_length=16)
    inputs = models.JSONField(default=dict)
    state = models.CharField(max_length=16, default="queued")
    stage = models.CharField(max_length=32, default="queued")
    bytes_processed = models.PositiveBigIntegerField(default=0)
    total_bytes = models.PositiveBigIntegerField(null=True)
    attempts = models.PositiveIntegerField(default=0)
    lease_id = models.UUIDField(null=True)
    lease_expires_at = models.DateTimeField(null=True)
    file = models.OneToOneField("DatasetFile", null=True, on_delete=models.PROTECT)
    error_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["dataset", "requested_by", "request_key"], name="dataset_import_idempotency")]
        indexes = [
            models.Index(fields=["state", "lease_expires_at", "created_at"], name="dataset_import_queue"),
            models.Index(fields=["dataset", "requested_by", "created_at"], name="dataset_import_owner"),
        ]


class DatasetTransfer(models.Model):
    """Durable capacity reservation. No credentials or file contents are stored here."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    dataset = models.ForeignKey("Dataset", on_delete=models.CASCADE)
    kind = models.CharField(max_length=16)  # write / export
    identity = models.CharField(max_length=64, unique=True)
    size_bytes = models.PositiveBigIntegerField()
    state = models.CharField(max_length=16, default="active")
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    storage_backend = models.CharField(max_length=32, blank=True)
    object_key = models.CharField(max_length=1024, blank=True)
    multipart_id = models.TextField(blank=True)
    file = models.ForeignKey("DatasetFile", null=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=["tenant", "kind", "state", "expires_at"], name="dataset_transfer_capacity")]


class Dataset(SoftDeleteModel):
    VISIBILITY_PUBLIC = "public"
    VISIBILITY_PRIVATE = "private"
    VISIBILITY_CHOICES = (
        (VISIBILITY_PUBLIC, "Public"),
        (VISIBILITY_PRIVATE, "Private"),
    )

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="datasets")
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name="datasets")
    name = models.CharField(max_length=255)
    visibility = models.CharField(max_length=32, choices=VISIBILITY_CHOICES, default=VISIBILITY_PRIVATE)
    size_bytes = models.PositiveBigIntegerField(default=0)
    file_count = models.PositiveIntegerField(default=0)
    current_version = models.CharField(max_length=32, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_datasets",
    )

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "visibility", "status"]),
            models.Index(fields=["tenant", "name", "status"]),
        ]

    def __str__(self) -> str:
        return self.name


class DatasetFile(SoftDeleteModel):
    STORAGE_LOCAL = "local"
    STORAGE_S3 = "s3"
    STORAGE_CHOICES = (
        (STORAGE_LOCAL, "Local"),
        (STORAGE_S3, "S3-compatible"),
    )

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="dataset_files")
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name="dataset_files")
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="files")
    file_name = models.CharField(max_length=255)
    storage_path = models.CharField(max_length=1024)
    object_key = models.CharField(max_length=1024, blank=True)
    storage_backend = models.CharField(max_length=32, choices=STORAGE_CHOICES, default=STORAGE_LOCAL)
    sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    content_type = models.CharField(max_length=255, blank=True)
    metadata_json = models.JSONField(default=dict, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_dataset_files",
    )

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "dataset", "status", "created_at"]),
            models.Index(fields=["tenant", "sha256"]),
            models.Index(fields=["dataset", "status", "created_at"]),
        ]

    def save(self, *args, **kwargs):  # type: ignore[override]
        if self.dataset_id and not self.tenant_id:
            self.tenant_id = self.dataset.tenant_id
        if self.dataset_id and not self.project_id:
            self.project_id = self.dataset.project_id
        if not self.object_key and self.storage_path:
            self.object_key = self.storage_path
        super().save(*args, **kwargs)


class DatasetVersion(SoftDeleteModel):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="dataset_versions")
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name="dataset_versions")
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="versions")
    version = models.CharField(max_length=32)
    file_count = models.PositiveIntegerField(default=0)
    size_bytes = models.PositiveBigIntegerField(default=0)
    snapshot_json = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_dataset_versions",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["dataset", "version"], name="unique_dataset_version"),
        ]
        indexes = [
            models.Index(fields=["tenant", "dataset", "created_at"]),
            models.Index(fields=["dataset", "created_at"]),
        ]

    def save(self, *args, **kwargs):  # type: ignore[override]
        if self.dataset_id and not self.tenant_id:
            self.tenant_id = self.dataset.tenant_id
        if self.dataset_id and not self.project_id:
            self.project_id = self.dataset.project_id
        super().save(*args, **kwargs)


class DatasetVersionEntry(models.Model):
    """Immutable per-file manifest, independent of mutable DatasetFile records."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.ForeignKey(DatasetVersion, on_delete=models.CASCADE, related_name="entries")
    file_id = models.UUIDField()
    created_at = models.DateTimeField()
    payload = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["version", "file_id"], name="dataset_version_entry_file")]
        indexes = [models.Index(fields=["version", "created_at", "id"], name="dataset_manifest_page")]


class DatasetQuota(SoftDeleteModel):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="dataset_quotas")
    dataset = models.OneToOneField(Dataset, on_delete=models.CASCADE, related_name="quota")
    max_size_bytes = models.PositiveBigIntegerField()
    raw_value = models.CharField(max_length=64)

    def save(self, *args, **kwargs):  # type: ignore[override]
        if self.dataset_id and not self.tenant_id:
            self.tenant_id = self.dataset.tenant_id
        super().save(*args, **kwargs)


class DatasetFileIndex(SoftDeleteModel):
    STATUS_PENDING = "pending"
    STATUS_INDEXED = "indexed"
    STATUS_FAILED = "failed"
    INDEX_STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_INDEXED, "Indexed"),
        (STATUS_FAILED, "Failed"),
    )

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="dataset_file_indexes")
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name="dataset_file_indexes")
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="file_indexes")
    file = models.OneToOneField(DatasetFile, on_delete=models.CASCADE, related_name="index")
    version = models.CharField(max_length=32, blank=True)
    index_status = models.CharField(max_length=32, choices=INDEX_STATUS_CHOICES, default=STATUS_PENDING)
    content_text = models.TextField(blank=True)
    content_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    error_code = models.CharField(max_length=64, blank=True)
    indexed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "dataset", "index_status", "created_at"]),
            models.Index(fields=["tenant", "content_sha256"]),
        ]


class MediaAsset(SoftDeleteModel):
    PURPOSE_CHAT_INPUT = "chat_input"
    PURPOSE_AGENT_ATTACHMENT = "agent_attachment"
    PURPOSE_DATASET_FILE = "dataset_file"
    PURPOSE_CHOICES = (
        (PURPOSE_CHAT_INPUT, "Chat Input"),
        (PURPOSE_AGENT_ATTACHMENT, "Agent Attachment"),
        (PURPOSE_DATASET_FILE, "Dataset File"),
    )

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="media_assets")
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name="media_assets")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_media_assets",
    )
    purpose = models.CharField(max_length=64, choices=PURPOSE_CHOICES, default=PURPOSE_CHAT_INPUT)
    file_name = models.CharField(max_length=255)
    storage_path = models.CharField(max_length=1024)
    content_type = models.CharField(max_length=255)
    size_bytes = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "project", "status", "created_at"]),
            models.Index(fields=["tenant", "purpose", "status", "created_at"]),
            models.Index(fields=["tenant", "sha256"]),
        ]



from .model_extension import model_extension


def __getattr__(name):
    if name == "DatasetPricing":
        return getattr(model_extension(), name)
    raise AttributeError(name)
