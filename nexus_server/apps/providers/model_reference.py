"""Provider model defaults inherited by Sources unless explicitly overridden."""
from django.conf import settings
from django.db import transaction
from rest_framework import exceptions, serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.audit.services import log_audit
from apps.common.resource_catalog import resource_access_payload
from apps.deployments.model_reference import identity
from apps.gateway import models_dev
from apps.gateway.model_metadata import mapping
from .connection_services import require_connection_runtime
from .models import ProviderRuntimeAccount, ProviderRuntimeModelOffer


def offer_access(request, account_id, offer_id, permission):
    account, runtime = require_connection_runtime(request=request, account_id=account_id)
    access = resource_access_payload(request=request, resource_type="provider_connection", obj=account)
    if not access.get(permission):
        raise exceptions.PermissionDenied("Provider access is required.")
    offer = runtime.model_offers.exclude(status="deleted").filter(pk=offer_id).first()
    if offer is None:
        raise exceptions.NotFound("Model not found.")
    return offer, runtime, access


def payload(offer, runtime, access):
    override = mapping(offer.metadata).get("models_dev") or None
    account = runtime.provider_account or runtime.source_provider_account
    provider_name = account.provider.name
    effective = {"provider": (override or {}).get("provider") or mapping(getattr(settings, "NEXUS_MODELS_DEV_PROVIDER_MAP", {})).get(provider_name, provider_name),
                 "model": (override or {}).get("model") or offer.upstream_model_id}
    catalog = models_dev.snapshot()
    reference = models_dev.lookup(catalog, effective["provider"], effective["model"])
    return {"mapping": override, "effective": effective, "matched": bool(reference), "capacity": reference,
            "updated_at": catalog.get("updated_at"), "can_edit": access.get("can_edit", False)}


class ProviderModelReferenceView(APIView):
    def get(self, request, account_id, offer_id):
        offer, runtime, access = offer_access(request, account_id, offer_id, "can_read")
        return Response(payload(offer, runtime, access), headers={"Cache-Control": "private, no-store"})

    @transaction.atomic
    def patch(self, request, account_id, offer_id):
        offer, runtime, access = offer_access(request, account_id, offer_id, "can_edit")
        if set(request.data) != {"mapping", "expected_mapping"}:
            raise serializers.ValidationError("mapping and expected_mapping are required.")
        value, expected = identity(request.data["mapping"]), identity(request.data["expected_mapping"])
        ProviderRuntimeAccount.objects.select_for_update().get(pk=runtime.pk)
        offer = ProviderRuntimeModelOffer.objects.select_for_update().get(pk=offer.pk)
        metadata = dict(mapping(offer.metadata))
        before = metadata.get("models_dev") or None
        if before != expected:
            return Response({"detail": "Mapping changed. Reload before saving.", "code": "MODEL_REFERENCE_CONFLICT"}, status=409)
        if value is None:
            metadata.pop("models_dev", None)
        else:
            metadata["models_dev"] = value
        offer.metadata = metadata
        offer.save(update_fields=["metadata", "updated_at"])
        log_audit(request=request, actor=request.user, action="providers.model_reference.update", resource_type="provider_connection",
                  resource_id=str(runtime.source_provider_account_id or runtime.provider_account_id or ""),
                  metadata={"offer_id": str(offer.pk), "before": before, "after": value})
        return Response(payload(offer, runtime, access), headers={"Cache-Control": "private, no-store"})
