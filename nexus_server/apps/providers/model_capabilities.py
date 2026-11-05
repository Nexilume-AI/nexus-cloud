from django.db import transaction
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.audit.services import log_audit
from apps.gateway import models_dev
from apps.gateway.model_capabilities import capabilities_payload, parse_update
from apps.gateway.model_info import provider_fields
from apps.gateway.model_metadata import mapping
from .model_reference import offer_access
from .models import ProviderRuntimeAccount, ProviderRuntimeModelOffer


def payload(offer, runtime, access):
    catalog = models_dev.snapshot()
    return capabilities_payload(override=mapping(offer.metadata).get("model_capabilities_override"),
        fields=provider_fields(offer, runtime, catalog), inherited=provider_fields(offer, runtime, catalog, include_override=False), access=access)


class ProviderModelCapabilitiesView(APIView):
    def get(self, request, account_id, offer_id):
        offer, runtime, access = offer_access(request, account_id, offer_id, "can_read")
        return Response(payload(offer, runtime, access), headers={"Cache-Control": "private, no-store"})

    @transaction.atomic
    def patch(self, request, account_id, offer_id):
        offer, runtime, access = offer_access(request, account_id, offer_id, "can_edit")
        value, expected = parse_update(request.data)
        ProviderRuntimeAccount.objects.select_for_update().get(pk=runtime.pk)
        offer = ProviderRuntimeModelOffer.objects.select_for_update().get(pk=offer.pk)
        metadata = dict(mapping(offer.metadata))
        before = metadata.get("model_capabilities_override")
        if before != expected:
            return Response({"detail": "Capabilities changed. Reload before saving.", "code": "MODEL_CAPABILITIES_CONFLICT"}, status=409)
        if value is None:
            metadata.pop("model_capabilities_override", None)
        else:
            metadata["model_capabilities_override"] = value
        offer.metadata = metadata
        offer.save(update_fields=["metadata", "updated_at"])
        log_audit(request=request, actor=request.user, action="providers.model_capabilities.update", resource_type="provider_connection",
                  resource_id=str(runtime.source_provider_account_id or runtime.provider_account_id or ""),
                  metadata={"offer_id": str(offer.pk), "before": before, "after": value})
        return Response(payload(offer, runtime, access), headers={"Cache-Control": "private, no-store"})

    def post(self, request, account_id, offer_id):
        """Refresh observations only. No inference probes, status or defaults changes."""
        from .runtime_services import discover_runtime_model_ids
        from .model_info import retain_capacity
        offer, runtime, access = offer_access(request, account_id, offer_id, "can_edit")
        if request.data:
            raise serializers.ValidationError("Metadata refresh takes no parameters.")
        source_key = getattr(runtime.source_provider_account, "encrypted_key", None)
        try:
            ids = discover_runtime_model_ids(runtime=runtime)
        except Exception:
            return Response({"detail": "Upstream metadata could not be refreshed. Saved observations are preserved.",
                             "code": "MODEL_METADATA_UNAVAILABLE"}, status=502)
        values = getattr(runtime, "_discovered_model_capacities", {}).get(offer.upstream_model_id, {})
        if offer.upstream_model_id not in ids or not any(v is not None for v in values.values()):
            return Response({"detail": "Upstream did not report capability metadata. Saved observations are preserved.",
                             "code": "MODEL_METADATA_UNAVAILABLE"}, status=422)
        with transaction.atomic():
            current = ProviderRuntimeAccount.objects.select_for_update().get(pk=runtime.pk)
            # Ignore an in-flight response from a replaced endpoint/credential.
            if any(getattr(current, name) != getattr(runtime, name) for name in
                   ("internal_api_url", "encrypted_proxy_api_key", "updated_at")) or getattr(current.source_provider_account, "encrypted_key", None) != source_key:
                return Response({"detail": "Provider changed during refresh. Please retry.", "code": "MODEL_CAPABILITIES_CONFLICT"}, status=409)
            offer = ProviderRuntimeModelOffer.objects.select_for_update().get(pk=offer.pk)
            retain_capacity(offer, runtime)
            offer.save(update_fields=["metadata", "updated_at"])
        log_audit(request=request, actor=request.user, action="providers.model_capabilities.refresh", resource_type="provider_connection",
                  resource_id=str(runtime.source_provider_account_id or runtime.provider_account_id or ""), metadata={"offer_id": str(offer.pk)})
        return Response(payload(offer, runtime, access), headers={"Cache-Control": "private, no-store"})
