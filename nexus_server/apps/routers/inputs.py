"""Shared operational Router input validation."""
from __future__ import annotations
from rest_framework import serializers
from apps.common.resource_catalog import ResourceOwnershipInputSerializer
from .models import Router


class RouterCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    router_type = serializers.ChoiceField(
        choices=[choice[0] for choice in Router.TYPE_CHOICES],
        required=False,
        default=Router.TYPE_EXECUTION,
    )
    ownership = ResourceOwnershipInputSerializer(required=False)
    strategy = serializers.ChoiceField(
        choices=[choice[0] for choice in Router.STRATEGY_CHOICES if choice[0] != Router.STRATEGY_CUSTOM],
        required=False,
        default=Router.STRATEGY_MANUAL_PRIORITY,
    )
    model_group_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=True,
    )

    def validate(self, attrs: dict) -> dict:
        if attrs["router_type"] == Router.TYPE_AGGREGATION and attrs.get("model_group_ids"):
            raise serializers.ValidationError("Aggregation Routers cannot be created with local Model Pools.")
        return attrs


class RouterUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    strategy = serializers.ChoiceField(
        choices=[choice[0] for choice in Router.STRATEGY_CHOICES],
        required=False,
    )
    model_group_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=True,
    )

    def validate(self, attrs: dict) -> dict:
        if not attrs:
            raise serializers.ValidationError("At least one update field is required.")
        return attrs


class RouterOutputUpdateSerializer(serializers.Serializer):
    model_name = serializers.RegexField(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", required=False)
    description = serializers.CharField(max_length=512, required=False, allow_blank=True)
    enabled = serializers.BooleanField(required=False)
    is_default = serializers.BooleanField(required=False)

    def validate(self, attrs: dict) -> dict:
        if not attrs:
            raise serializers.ValidationError("At least one output field is required.")
        return attrs


class RouterChildBindingCreateSerializer(serializers.Serializer):
    exposed_model_name = serializers.RegexField(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    child_output_id = serializers.UUIDField()
    priority = serializers.IntegerField(min_value=0, required=False)
    weight = serializers.IntegerField(min_value=1, required=False, default=100)


class RouterChildBindingUpdateSerializer(serializers.Serializer):
    exposed_model_name = serializers.RegexField(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", required=False)
    enabled = serializers.BooleanField(required=False)
    priority = serializers.IntegerField(min_value=0, required=False)
    weight = serializers.IntegerField(min_value=1, required=False)

    def validate(self, attrs: dict) -> dict:
        if not attrs:
            raise serializers.ValidationError("At least one child binding field is required.")
        return attrs


class RouterModelGroupBindSerializer(serializers.Serializer):
    providers = serializers.ListField(child=serializers.CharField(max_length=255), required=False, allow_empty=True)
    model_group_ids = serializers.ListField(child=serializers.UUIDField(), required=False, allow_empty=True)

    def validate(self, attrs: dict) -> dict:
        if "providers" not in attrs and "model_group_ids" not in attrs:
            raise serializers.ValidationError("providers or model_group_ids is required.")
        return attrs


class RouterPolicySerializer(serializers.Serializer):
    strategy = serializers.ChoiceField(choices=[choice[0] for choice in Router.STRATEGY_CHOICES])
