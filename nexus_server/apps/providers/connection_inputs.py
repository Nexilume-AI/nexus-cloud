"""Shared Provider connection inputs and operational status helpers."""
from __future__ import annotations
from rest_framework import serializers
from apps.common.resource_catalog import ResourceOwnershipInputSerializer
from apps.gateway.provider_http import ProviderEndpointRejected, validate_provider_base_url
from .models import ProviderAccount, ProviderRuntimeAccount, ProviderRuntimeModelOffer


class ProviderRuntimeModelOfferUpdateSerializer(serializers.Serializer):
    image_pricing = serializers.ListField(child=serializers.DictField(), max_length=64, required=False)

    def validate_image_pricing(self, value):
        from apps.gateway.image_serializers import ImagePricingRowSerializer
        validator = ImagePricingRowSerializer(data=value, many=True)
        validator.is_valid(raise_exception=True)
        rows = validator.data
        keys = [(row["operation"], row["size"], row["quality"]) for row in rows]
        if len(set(keys)) != len(keys):
            raise serializers.ValidationError("Duplicate image pricing combination.")
        return list(rows)

    canonical_model_id = serializers.UUIDField(required=False, allow_null=True)
    status = serializers.ChoiceField(
        choices=[
            ProviderRuntimeModelOffer.STATUS_DETECTED,
            ProviderRuntimeModelOffer.STATUS_CONFIRMED,
            ProviderRuntimeModelOffer.STATUS_DISABLED,
        ],
        required=False,
    )
    price_per_1k_tokens = serializers.DecimalField(max_digits=18, decimal_places=6, required=False)
    input_price_per_1k_tokens = serializers.DecimalField(max_digits=18, decimal_places=6, required=False, allow_null=True)
    output_price_per_1k_tokens = serializers.DecimalField(max_digits=18, decimal_places=6, required=False, allow_null=True)
    cached_input_price_per_1k_tokens = serializers.DecimalField(max_digits=18, decimal_places=6, required=False, allow_null=True)
    reasoning_output_price_per_1k_tokens = serializers.DecimalField(max_digits=18, decimal_places=6, required=False, allow_null=True)
    daily_limit = serializers.IntegerField(min_value=0, required=False)
    monthly_limit = serializers.IntegerField(min_value=0, required=False)
    capacity = serializers.IntegerField(min_value=0, required=False)
    capabilities = serializers.ListField(child=serializers.CharField(max_length=64), required=False, max_length=32)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("At least one Model Offer field is required.")
        if attrs.get("status") == ProviderRuntimeModelOffer.STATUS_CONFIRMED and not (
            attrs.get("canonical_model_id") or getattr(self.context.get("offer"), "canonical_model_id", None)
        ):
            raise serializers.ValidationError("Map a Canonical Model before confirming this Offer.")
        return attrs


class ProviderConnectionCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    account_id = serializers.CharField(max_length=128, required=False, allow_blank=True)
    engine = serializers.ChoiceField(choices=["direct_api", "codex_proxy", "cliproxyapi"])
    ownership = ResourceOwnershipInputSerializer(required=False)
    upstream_provider = serializers.CharField(max_length=64, required=False, allow_blank=True)
    url = serializers.URLField(max_length=1024, required=False, allow_blank=True)
    key = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False, write_only=True)

    def validate(self, attrs):
        if attrs["engine"] == "direct_api":
            if not attrs.get("url"):
                raise serializers.ValidationError({"url": "API URL is required for Direct API."})
            if not attrs.get("key"):
                raise serializers.ValidationError({"key": "API key is required for Direct API."})
            try:
                attrs["url"] = validate_provider_base_url(attrs["url"])
            except ProviderEndpointRejected as exc:
                raise serializers.ValidationError({"url": str(exc)}) from exc
        if attrs["engine"] == "cliproxyapi" and attrs.get("upstream_provider", "").lower() not in {"openai", "claude"}:
            raise serializers.ValidationError({"upstream_provider": "Choose OpenAI or Claude for CLIProxyAPI."})
        return attrs


class ProviderConnectionUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    engine = serializers.ChoiceField(choices=["direct_api", "codex_proxy", "cliproxyapi"], required=False)
    upstream_provider = serializers.CharField(max_length=64, required=False, allow_blank=False)
    url = serializers.URLField(max_length=1024, required=False, allow_blank=True)
    key = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False, write_only=True)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("At least one Provider field is required.")
        if attrs.get("engine") == "cliproxyapi" and "upstream_provider" in attrs:
            if attrs["upstream_provider"].lower() not in {"openai", "claude"}:
                raise serializers.ValidationError({"upstream_provider": "Choose OpenAI or Claude for CLIProxyAPI."})
        if "url" in attrs and attrs["url"]:
            try:
                attrs["url"] = validate_provider_base_url(attrs["url"])
            except ProviderEndpointRejected as exc:
                raise serializers.ValidationError({"url": str(exc)}) from exc
        return attrs


class ProviderConnectionRemoveSerializer(serializers.Serializer):
    confirmation_name = serializers.CharField(required=False, allow_blank=True, default="")


def _provider_account_engine(account):
    if account.auth_mode == ProviderAccount.AUTH_API_KEY:
        return ProviderRuntimeAccount.RUNTIME_DIRECT_API
    if account.preferred_runtime_type == ProviderAccount.PREFERRED_RUNTIME_CLIPROXYAPI:
        return ProviderRuntimeAccount.RUNTIME_CLIPROXYAPI
    return ProviderRuntimeAccount.RUNTIME_CODEX_PROXY


def _provider_health_history(runtime):
    if runtime is None:
        return []
    checks = getattr(runtime, "prefetched_health_checks", None)
    if checks is None:
        checks = runtime.health_checks.order_by("-checked_at")[:8]
    return [
        {
            "status": check.status,
            "reason": check.reason,
            "latency_ms": check.latency_ms,
            "checked_at": check.checked_at,
        }
        for check in checks
    ]


def _provider_connection_status(*, account, runtime):
    if runtime is None:
        return "repair_required"
    if runtime.status in {"failed", "unhealthy", "login_required", "starting", "stopping", "stopped", "created"}:
        return runtime.status
    if account.login_status in {"failed", "login_required", "logging_in"}:
        return account.login_status
    return runtime.status


def _provider_connection_message(*, account, runtime):
    if runtime is None:
        return "The Provider runtime is missing and needs repair."
    if runtime.last_error:
        return runtime.last_error
    if account.last_login_error:
        return account.last_login_error
    if runtime.status == ProviderRuntimeAccount.STATUS_ACTIVE:
        return "Provider is operational."
    return f"Provider is {runtime.status.replace('_', ' ')}."

