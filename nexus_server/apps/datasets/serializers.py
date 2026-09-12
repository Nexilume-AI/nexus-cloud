from __future__ import annotations

from rest_framework import serializers

from apps.common.resource_catalog import ResourceOwnershipInputSerializer, resource_context_payload
from apps.common.models import SoftDeleteModel

from .models import Dataset, DatasetFile, DatasetQuota, DatasetVersion, MediaAsset


class DatasetCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    ownership = ResourceOwnershipInputSerializer(required=False)


class DatasetVersionCreateSerializer(serializers.Serializer):
    release_notes = serializers.CharField(max_length=20_000, required=False, allow_blank=True, default="")


class DatasetRenameSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)


class DatasetVisibilitySerializer(serializers.Serializer):
    visibility = serializers.ChoiceField(choices=Dataset.VISIBILITY_CHOICES)


class DatasetFileImportSerializer(serializers.Serializer):
    file = serializers.FileField(allow_empty_file=False)
    rights_confirmed = serializers.BooleanField(required=True)


class DatasetFileSerializer(serializers.ModelSerializer):
    tenant_id = serializers.UUIDField(source="tenant.id", read_only=True)
    project_id = serializers.UUIDField(source="project.id", read_only=True, allow_null=True)
    dataset_id = serializers.UUIDField(source="dataset.id", read_only=True)
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = DatasetFile
        fields = [
            "id",
            "tenant_id",
            "project_id",
            "dataset_id",
            "file_name",
            "size_bytes",
            "content_type",
            "sha256",
            "storage_backend",
            "metadata_json",
            "download_url",
            "status",
            "created_at",
            "updated_at",
        ]

    def get_download_url(self, obj: DatasetFile) -> str:
        return f"/api/v1/datasets/{obj.dataset_id}/files/{obj.id}/download/"


class DatasetVersionSerializer(serializers.ModelSerializer):
    tenant_id = serializers.UUIDField(source="tenant.id", read_only=True)
    project_id = serializers.UUIDField(source="project.id", read_only=True, allow_null=True)
    snapshot_json = serializers.SerializerMethodField()

    class Meta:
        model = DatasetVersion
        fields = [
            "id",
            "tenant_id",
            "project_id",
            "version",
            "file_count",
            "size_bytes",
            "snapshot_json",
            "status",
            "created_at",
            "updated_at",
        ]

    def get_snapshot_json(self, obj: DatasetVersion) -> dict:
        from .manifests import raw_entries, public_entry
        snapshot = obj.snapshot_json if isinstance(obj.snapshot_json, dict) else {}
        if self.context.get("compact"):
            return {"files": [], "release_notes": str(snapshot.get("release_notes") or ""),
                "paginated": True, "summary": snapshot.get("summary", {})}
        return {"files": [public_entry(obj, row) for row in raw_entries(obj)], "release_notes": str(snapshot.get("release_notes") or "")}


class DatasetQuotaSerializer(serializers.ModelSerializer):
    max_size = serializers.CharField(source="raw_value")

    class Meta:
        model = DatasetQuota
        fields = ["id", "max_size", "max_size_bytes", "status", "created_at", "updated_at"]


class DatasetQuotaSetSerializer(serializers.Serializer):
    max_size = serializers.CharField(max_length=64)


class MediaAssetSerializer(serializers.ModelSerializer):
    tenant_id = serializers.UUIDField(source="tenant.id", read_only=True)
    project_id = serializers.UUIDField(source="project.id", read_only=True, allow_null=True)
    uri = serializers.SerializerMethodField()

    class Meta:
        model = MediaAsset
        fields = [
            "id",
            "tenant_id",
            "project_id",
            "purpose",
            "file_name",
            "content_type",
            "size_bytes",
            "sha256",
            "uri",
            "status",
            "expires_at",
            "created_at",
            "updated_at",
        ]

    def get_uri(self, obj: MediaAsset) -> str:
        return f"nexus-media://{obj.id}"


class MediaAssetCreateSerializer(serializers.Serializer):
    purpose = serializers.ChoiceField(choices=MediaAsset.PURPOSE_CHOICES, required=False, default=MediaAsset.PURPOSE_CHAT_INPUT)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)


class MediaAssetSignedURLSerializer(serializers.Serializer):
    expires_in = serializers.IntegerField(required=False, min_value=1, max_value=3600)


class AgentTraceExportSerializer(serializers.Serializer):
    agent_id = serializers.UUIDField()
    run_id = serializers.UUIDField()


class AgentMemoryExportSerializer(serializers.Serializer):
    agent_id = serializers.UUIDField()
    memory_item_ids = serializers.ListField(
        child=serializers.UUIDField(),
        max_length=2000,
        required=True,
        allow_empty=False,
        error_messages={"empty": "Select at least one memory item to export."},
    )


class AgentArtifactCaptureSerializer(serializers.Serializer):
    agent_id = serializers.UUIDField()
    artifact_id = serializers.UUIDField()


from .presentation import configured_dataset_serializer, serializer_export

DatasetSerializer = configured_dataset_serializer()


def __getattr__(name):
    # Legacy imports remain available only when the selected edition provides them.
    if name in ["dataset_listing_profile_checks","DatasetAcquisitionCreateSerializer","DatasetPricingSerializer","DatasetPricingSetSerializer","MarketplaceDatasetSerializer","MarketplaceDatasetFileSerializer","MarketplaceDatasetSnapshotFileSerializer"]:
        return serializer_export(name)
    raise AttributeError(name)
