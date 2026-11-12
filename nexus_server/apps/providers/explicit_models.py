"""Operator-declared Direct API models, independent of upstream discovery."""
from django.db import transaction
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.common.resource_limits import enforce_capability
from .connection_services import require_connection_runtime
from .connection_views import _connection_response
from .models import ProviderRuntimeAccount, ProviderRuntimeModelOffer
from .runtime_services import (get_provider_runtime, require_provider_runtime_admin,
    _canonical_model_for_upstream, log_runtime_write, ProviderRuntimeError)


class ExplicitModelInput(serializers.Serializer):
    upstream_model_id = serializers.CharField(max_length=128)

    def validate(self, attrs):
        if set(self.initial_data) - {"upstream_model_id"}:
            raise serializers.ValidationError("Only upstream_model_id is accepted.")
        if any(ord(c) < 33 for c in attrs["upstream_model_id"]):
            raise serializers.ValidationError("Model ID must not contain whitespace or control characters.")
        return attrs


@transaction.atomic
def add_explicit_model(*, request, runtime_id, upstream_model_id):
    runtime = get_provider_runtime(request=request, runtime_id=runtime_id)
    require_provider_runtime_admin(request=request, tenant=runtime.tenant)
    runtime = ProviderRuntimeAccount.objects.select_for_update(of=("self",)).select_related(
        "source_provider_account__provider").get(pk=runtime.pk)
    if runtime.runtime_type != ProviderRuntimeAccount.RUNTIME_DIRECT_API:
        raise ProviderRuntimeError("Explicit models require a Direct API Provider.")
    # Never revive disabled/deleted entries through an Add action.
    if runtime.model_offers.filter(upstream_model_id=upstream_model_id).exists():
        raise ProviderRuntimeError("This model already exists. Manage the existing model instead.")
    enforce_capability(tenant=runtime.tenant, code="models.model_offers", requested=1)
    native = runtime.source_provider_account.provider.name in {"system_one", "system-one"}
    offer = ProviderRuntimeModelOffer.objects.create(runtime_account=runtime,
        canonical_model=_canonical_model_for_upstream(upstream_model_id), upstream_model_id=upstream_model_id,
        status="detected", health_status="degraded",
        health_reason="Explicitly declared by the Provider administrator; inference has not been verified.",
        metadata={"discovery_source": "explicit", "model_contract": {
            "operations": ["systemone"] if native else ["chat.completions", "responses"],
            "input_modalities": ["text"], "output_modalities": ["json"] if native else ["text"]}})
    log_runtime_write(request=request, runtime=runtime, action="providers.runtime.model_offer.add_explicit",
        metadata={"model_offer_id": str(offer.pk), "upstream_model_id": upstream_model_id})
    return offer


class ProviderExplicitModelView(APIView):
    def post(self, request, account_id):
        serializer = ExplicitModelInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        _, runtime = require_connection_runtime(request=request, account_id=account_id)
        add_explicit_model(request=request, runtime_id=str(runtime.pk), **serializer.validated_data)
        return _connection_response(request, account_id)
