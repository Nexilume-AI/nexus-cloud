"""Personal file lifecycle, with no Marketplace setup or acquisition fields."""
from rest_framework import serializers
from rest_framework import exceptions
from django.db.models import Q
from apps.common.resource_catalog import resource_context_payload
from apps.common.resource_facts import ownership_payload
from apps.datasets.models import Dataset, DatasetQuota
from apps.datasets.services import can_manage_dataset


class DatasetSerializer(serializers.ModelSerializer):
    tenant_id = serializers.UUIDField(source="tenant.id", read_only=True)
    project_id = serializers.UUIDField(source="project.id", read_only=True, allow_null=True)
    ownership = serializers.SerializerMethodField()
    access = serializers.SerializerMethodField()
    lifecycle_status = serializers.SerializerMethodField()
    quota = serializers.SerializerMethodField()
    allowed_actions = serializers.SerializerMethodField()

    class Meta:
        model = Dataset
        fields = ["id", "tenant_id", "project_id", "name", "visibility", "ownership", "access",
                  "status", "size_bytes", "file_count", "current_version", "lifecycle_status",
                  "quota", "allowed_actions", "created_at", "updated_at"]

    def get_ownership(self, obj):
        if obj.status == "deleted":
            return self._deleted_context["ownership"]
        return resource_context_payload(request=self.context.get("request"), resource_type="dataset", obj=obj)["ownership"]

    def get_access(self, obj):
        if obj.status == "deleted":
            return self._deleted_context["access"]
        return resource_context_payload(request=self.context.get("request"), resource_type="dataset", obj=obj)["access"]

    def to_representation(self, obj):
        if obj.status == "deleted":
            from .resource_catalog import PersonalResourceCatalog
            # DELETE returns a tombstone, not an active catalog resource. Read
            # current ownership again; never authorize from a stale object.
            user, tenant_id, project_id = PersonalResourceCatalog()._context(
                self.context.get("request"), obj.tenant_id)
            current = Dataset.objects.filter(pk=obj.pk, tenant_id=tenant_id, status="deleted").filter(
                Q(project_id=project_id) | Q(project__isnull=True)).filter(
                Q(created_by=user) | Q(created_by__isnull=True)).select_related("project").first()
            if current is None:
                raise exceptions.NotFound("Resource not found.")
            obj = current
            self._deleted_context = {"ownership": ownership_payload(project=current.project),
                "access": {"can_discover": False, "can_read": False, "can_use": False,
                           "can_edit": False, "can_manage": False, "sources": ["personal_owner"]}}
        return super().to_representation(obj)

    def get_lifecycle_status(self, obj):
        if not obj.file_count:
            return "draft"
        return "versioned" if obj.current_version else "assets_added"

    def get_quota(self, obj):
        try:
            quota = obj.quota
        except DatasetQuota.DoesNotExist:
            return {"max_size": "", "max_size_bytes": None}
        return {"max_size": quota.raw_value, "max_size_bytes": quota.max_size_bytes}

    def get_allowed_actions(self, obj):
        request = self.context.get("request")
        if request is None or not can_manage_dataset(user=request.user, dataset=obj):
            return []
        actions = ["pull", "rename", "import_asset", "set_quota", "delete"]
        if obj.file_count:
            actions.append("create_release")
        return actions
