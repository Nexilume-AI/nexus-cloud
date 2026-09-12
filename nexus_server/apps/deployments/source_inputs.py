from __future__ import annotations
from rest_framework import serializers
from apps.common.models import SoftDeleteModel
from apps.common.resource_catalog import resource_context_payload
from apps.deployments.models import CanonicalModel, Deployment, DeploymentHealthCheck, ModelGroup, ModelGroupDeployment

class CanonicalModelSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        fields = ("operations", "input_modalities", "output_modalities")
        if any(field in attrs for field in fields):
            from apps.gateway.image_serializers import ModelContractSerializer
            contract = {field: attrs.get(field, getattr(self.instance, field, [])) for field in fields}
            validator = ModelContractSerializer(data=contract)
            validator.is_valid(raise_exception=True)
        return attrs

    class Meta:
        model = CanonicalModel
        fields = [
            "id", "key", "display_name", "family", "modalities", "capabilities",
            "input_modalities", "output_modalities", "operations",
            "context_window", "status", "metadata", "created_at", "updated_at",
        ]


class CanonicalModelMergeSerializer(serializers.Serializer):
    target_model_id = serializers.UUIDField()


class DeploymentHealthCheckSerializer(serializers.ModelSerializer):
    deployment_id = serializers.CharField(source="deployment.deployment_id", read_only=True)

    class Meta:
        model = DeploymentHealthCheck
        fields = ["id", "deployment_id", "status", "reason", "latency_ms", "checked_at"]


class ModelSourceNewPoolSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=128)
    visibility = serializers.ChoiceField(
        choices=Deployment.VISIBILITY_CHOICES,
        required=False,
    )


class ModelSourceBatchItemSerializer(serializers.Serializer):
    model_offer_id = serializers.UUIDField()
    source_id = serializers.CharField(max_length=128)
    visibility = serializers.ChoiceField(choices=Deployment.VISIBILITY_CHOICES, required=False, default=Deployment.VISIBILITY_PRIVATE)
    model_group_id = serializers.UUIDField(required=False, allow_null=True)
    new_pool = ModelSourceNewPoolSerializer(required=False)

    def validate(self, attrs: dict) -> dict:
        if bool(attrs.get("model_group_id")) == bool(attrs.get("new_pool")):
            raise serializers.ValidationError("Choose exactly one existing Model Pool or new_pool.")
        new_pool = attrs.get("new_pool")
        if new_pool is not None:
            name = str(new_pool.get("name") or "").strip()
            if not name:
                raise serializers.ValidationError({"new_pool": "name is required."})
            attrs["new_pool"] = {
                "name": name,
                "visibility": new_pool.get("visibility") or attrs["visibility"],
            }
        return attrs


class DeploymentUpdateSerializer(serializers.Serializer):
    provider = serializers.CharField(max_length=64, required=False)
    canonical_model_id = serializers.UUIDField(required=False)
    upstream_model_id = serializers.CharField(max_length=255, required=False)
    endpoint = serializers.URLField(max_length=1024, required=False, allow_blank=True)

    def validate(self, attrs: dict) -> dict:
        if "model" in self.initial_data:
            raise serializers.ValidationError({"model": "Unsupported field; use canonical_model_id and upstream_model_id."})
        if not attrs:
            raise serializers.ValidationError("At least one update field is required.")
        if "deployment_id" in self.initial_data:
            raise serializers.ValidationError("deployment_id cannot be updated.")
        return attrs


class DeploymentVisibilitySerializer(serializers.Serializer):
    visibility = serializers.ChoiceField(choices=Deployment.VISIBILITY_CHOICES)


class DeploymentPricingSerializer(serializers.Serializer):
    pricing_rate = serializers.DecimalField(max_digits=18, decimal_places=6)


class ModelGroupRoutingSerializer(serializers.Serializer):
    expected_revision = serializers.IntegerField(min_value=1)
    routing_strategy = serializers.ChoiceField(choices=ModelGroup.ROUTING_CHOICES)
    routing_config = serializers.JSONField(required=False)
    sources = serializers.ListField(child=serializers.DictField(), required=False)

    def validate_routing_config(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Routing config must be an object.")
        if value:
            raise serializers.ValidationError("Custom routing config is not supported yet. Remove unknown settings before applying this policy.")
        return value

    def validate_sources(self, value):
        normalized = []
        seen = set()
        allowed = {"id", "enabled", "priority", "weight", "fallback_order"}
        for index, item in enumerate(value):
            unknown = set(item) - allowed
            if unknown:
                raise serializers.ValidationError(f"Source {index + 1} contains unsupported fields: {', '.join(sorted(unknown))}.")
            source_id = serializers.UUIDField().run_validation(item.get("id"))
            if source_id in seen:
                raise serializers.ValidationError(f"Source {source_id} is listed more than once.")
            seen.add(source_id)
            normalized_item = {"id": source_id}
            if "enabled" in item:
                normalized_item["enabled"] = serializers.BooleanField().run_validation(item["enabled"])
            for field in ("priority", "weight", "fallback_order"):
                if field in item:
                    normalized_item[field] = serializers.IntegerField(min_value=0).run_validation(item[field])
            normalized.append(normalized_item)
        return normalized


class ModelGroupSourceRoutingSerializer(serializers.Serializer):
    expected_revision = serializers.IntegerField(min_value=1, required=False)
    enabled = serializers.BooleanField(required=False)
    priority = serializers.IntegerField(min_value=0, required=False)
    weight = serializers.IntegerField(min_value=0, required=False)
    fallback_order = serializers.IntegerField(min_value=0, required=False)

    def validate(self, attrs: dict) -> dict:
        if not {"enabled", "priority", "weight", "fallback_order"}.intersection(attrs):
            raise serializers.ValidationError("At least one source routing field is required.")
        if "expected_revision" not in attrs:
            raise serializers.ValidationError({"expected_revision": "This field is required."})
        return attrs



class BatchSourceValidationMixin:
    def validate_sources(self, value: list[dict]) -> list[dict]:
        offer_ids = [str(item["model_offer_id"]) for item in value]
        source_ids = [item["source_id"] for item in value]
        if len(offer_ids) != len(set(offer_ids)):
            raise serializers.ValidationError("Each Model Offer can only be selected once per batch.")
        if len(source_ids) != len(set(source_ids)):
            raise serializers.ValidationError("Source IDs must be unique within a batch.")
        return value

