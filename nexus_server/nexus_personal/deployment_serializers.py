"""Personal operational Source/Pool schemas, with current-row owner checks."""
from django.db.models import Q
from rest_framework import exceptions, serializers
from apps.common.resource_catalog import resource_context_payload
from apps.deployments.models import Deployment, ModelGroup, ModelGroupDeployment
from apps.deployments.source_inputs import (BatchSourceValidationMixin, ModelSourceNewPoolSerializer,
                                          ModelSourceBatchItemSerializer)
from .deployment_integration import PersonalDeploymentIntegration
from .resource_catalog import PersonalResourceCatalog


def response_context(request):
    return {"context": {"request": request}}


class PersonalInput:
    def to_internal_value(self, data):
        if isinstance(data, dict) and set(data) - set(self.fields):
            raise serializers.ValidationError({"fields": "This request contains unsupported personal Source fields."})
        return super().to_internal_value(data)


class DeploymentCreateSerializer(PersonalInput, serializers.Serializer):
    source_type = serializers.ChoiceField(choices=["provider_runtime"])
    provider_runtime_id = serializers.UUIDField()
    model_offer_id = serializers.UUIDField()
    deployment_id = serializers.CharField(max_length=128, required=False, allow_blank=True)
    model_group_id = serializers.UUIDField(required=False, allow_null=True)
    model_group_name = serializers.CharField(max_length=128, required=False, allow_blank=True)
    visibility = serializers.ChoiceField(choices=["private"], default="private")

    def validate(self, attrs):
        if attrs.get("model_group_id") and attrs.get("model_group_name"):
            raise serializers.ValidationError("Select an existing Pool or name a new Pool, not both.")
        return attrs


class PersonalNewPoolSerializer(PersonalInput, ModelSourceNewPoolSerializer):
    visibility = serializers.ChoiceField(choices=["private"], default="private")


class PersonalSourceBatchItemSerializer(PersonalInput, ModelSourceBatchItemSerializer):
    visibility = serializers.ChoiceField(choices=["private"], default="private")
    new_pool = PersonalNewPoolSerializer(required=False)


class ModelSourceBatchSerializer(PersonalInput, serializers.Serializer):
    origin = serializers.DictField()
    sources = PersonalSourceBatchItemSerializer(many=True, min_length=1, max_length=50)
    validate_sources = BatchSourceValidationMixin.validate_sources

    def validate_origin(self, value):
        if set(value) != {"type", "provider_runtime_id"} or value.get("type") != "provider_runtime":
            raise serializers.ValidationError("Select a discovered local Provider Runtime.")
        return {"type": "provider_runtime",
                "provider_runtime_id": str(serializers.UUIDField().run_validation(value.get("provider_runtime_id")))}


class DeploymentSerializer(serializers.ModelSerializer):
    tenant_id = serializers.UUIDField(read_only=True)
    project_id = serializers.UUIDField(read_only=True, allow_null=True)
    provider = serializers.SlugRelatedField(slug_field="name", read_only=True)
    provider_account_id = serializers.CharField(source="provider_account.account_id", read_only=True, allow_null=True)
    canonical_model_id = serializers.UUIDField(read_only=True)
    canonical_model_key = serializers.CharField(source="canonical_model.key", read_only=True)
    provider_runtime_id = serializers.UUIDField(read_only=True, allow_null=True)
    runtime_model_offer_id = serializers.UUIDField(read_only=True, allow_null=True)
    source_type = serializers.SerializerMethodField()

    class Meta:
        model = Deployment
        fields = ["id", "tenant_id", "project_id", "deployment_id", "provider", "provider_account_id",
                  "canonical_model_id", "canonical_model_key", "upstream_model_id", "provider_runtime_id",
                  "runtime_model_offer_id", "source_type", "endpoint", "visibility", "pricing_rate", "status",
                  "health_status", "health_reason", "last_success_at", "last_failure_at", "consecutive_failures",
                  "last_latency_ms", "last_checked_at", "created_at", "updated_at"]

    def get_source_type(self, obj):
        return "provider_runtime" if obj.provider_runtime_id else "manual"

    def to_representation(self, instance):
        request = self.context.get("request")
        user, tenant_id, project_id = PersonalResourceCatalog()._context(request, instance.tenant_id)
        if instance.status == "deleted":
            # A successful DELETE may return its tombstone, never stale endpoint
            # details. Other HTTP actions cannot serialize deleted resources.
            allowed = Deployment.objects.filter(pk=instance.pk, tenant_id=tenant_id, project_id=project_id,
                status="deleted").filter(Q(created_by=user) | Q(created_by__isnull=True)).first()
            if getattr(request, "method", "") != "DELETE" or allowed is None:
                raise exceptions.NotFound("Source not found.")
            return {"id": str(allowed.pk), "deployment_id": allowed.deployment_id, "status": "deleted"}
        current = PersonalDeploymentIntegration().visible_deployments(user=user, tenant=tenant_id).filter(pk=instance.pk).first()
        if current is None:
            raise exceptions.NotFound("Source not found.")
        payload = super().to_representation(current)
        payload.update(resource_context_payload(request=request, resource_type="deployment", obj=current))
        return payload


class ModelGroupDeploymentSerializer(serializers.ModelSerializer):
    deployment = DeploymentSerializer(read_only=True)

    class Meta:
        model = ModelGroupDeployment
        fields = ["id", "deployment", "enabled", "priority", "weight", "fallback_order", "last_selected_at",
                  "selection_count", "status"]

    def to_representation(self, instance):
        PersonalResourceCatalog()._resource(request=self.context.get("request"), resource_type="model_group", obj=instance.model_group)
        return super().to_representation(instance)


class ModelGroupSerializer(serializers.ModelSerializer):
    tenant_id = serializers.UUIDField(read_only=True)
    project_id = serializers.UUIDField(read_only=True, allow_null=True)
    canonical_model_id = serializers.UUIDField(read_only=True)
    canonical_model_key = serializers.CharField(source="canonical_model.key", read_only=True)
    deployments = serializers.SerializerMethodField()

    class Meta:
        model = ModelGroup
        fields = ["id", "tenant_id", "project_id", "name", "display_name", "canonical_model_id",
                  "canonical_model_key", "visibility", "routing_strategy", "routing_config", "routing_revision",
                  "status", "deployments"]

    def get_deployments(self, obj):
        request = self.context["request"]
        allowed = PersonalDeploymentIntegration().visible_deployments(user=request.user, tenant=obj.tenant_id).filter(
            project_id=obj.project_id, canonical_model_id=obj.canonical_model_id)
        links = obj.deployment_links.filter(status="active", deployment_id__in=allowed.values("pk")).select_related(
            "deployment", "deployment__provider", "deployment__provider_account", "deployment__canonical_model", "model_group")
        return ModelGroupDeploymentSerializer(links, many=True, context=self.context).data

    def to_representation(self, instance):
        request = self.context.get("request")
        current, _ = PersonalResourceCatalog()._resource(request=request, resource_type="model_group", obj=instance)
        payload = super().to_representation(current)
        payload.update(resource_context_payload(request=request, resource_type="model_group", obj=current))
        return payload
