"""Source-scoped reference identity; never changes routing or upstream credentials."""
from django.db import transaction
from rest_framework import exceptions, serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.audit.services import log_audit
from apps.common.resource_catalog import resource_access_payload
from apps.gateway import models_dev
from apps.gateway.model_info import source_reference_identity
from .models import Deployment
from .services import get_deployment


def identity(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"provider", "model"}:
        raise serializers.ValidationError("Provide both provider and model IDs, or null to restore the default.")
    result = {}
    for key, limit in (("provider", 200), ("model", 255)):
        raw = value[key]
        if not isinstance(raw, str) or not raw.strip() or len(raw.strip()) > limit or any(ord(c) < 32 for c in raw):
            raise serializers.ValidationError({key: "Enter a valid exact ID."})
        result[key] = raw.strip()
    return result


def source_access(request, deployment_id, action):
    source = get_deployment(request=request, deployment_identifier=deployment_id)
    access = resource_access_payload(request=request, resource_type="deployment", obj=source)
    if not access.get(action):
        raise exceptions.PermissionDenied("Source access is required.")
    return source, access


def reference_payload(source, access):
    catalog = models_dev.snapshot()
    effective = source_reference_identity(source)
    reference = models_dev.lookup(catalog, effective["provider"], effective["model"])
    return {"mapping": source.models_dev_mapping, "effective": effective, "matched": bool(reference),
            "capacity": reference, "updated_at": catalog.get("updated_at"), "can_edit": access.get("can_edit", False)}


class SourceModelReferenceView(APIView):
    def get(self, request, deployment_id):
        source, access = source_access(request, deployment_id, "can_read")
        return Response(reference_payload(source, access), headers={"Cache-Control": "private, no-store"})

    @transaction.atomic
    def patch(self, request, deployment_id):
        source, access = source_access(request, deployment_id, "can_edit")
        if set(request.data) != {"mapping", "expected_mapping"}:
            raise serializers.ValidationError("mapping and expected_mapping are required.")
        value, expected = identity(request.data["mapping"]), identity(request.data["expected_mapping"])
        source = Deployment.objects.select_for_update().get(pk=source.pk)
        if source.models_dev_mapping != expected:
            return Response({"detail": "Mapping changed. Reload the Source before saving.", "code": "MODEL_REFERENCE_CONFLICT"}, status=409)
        before = source.models_dev_mapping
        source.models_dev_mapping = value
        source.save(update_fields=["models_dev_mapping", "updated_at"])
        log_audit(request=request, actor=request.user, action="deployments.model_reference.update",
                  resource_type="deployment", resource_id=str(source.pk), metadata={"before": before, "after": value})
        return Response(reference_payload(source, access), headers={"Cache-Control": "private, no-store"})


class ModelReferenceCatalogView(APIView):
    def get(self, request):
        catalog = models_dev.snapshot()
        query = str(request.query_params.get("q", "")).strip()
        provider = str(request.query_params.get("provider", ""))
        if len(query) > 255 or len(provider) > 200:
            raise serializers.ValidationError("Search is too long.")
        try:
            offset = int(request.query_params.get("offset", 0))
            if offset < 0 or offset > 50000:
                raise ValueError()
        except (ValueError, TypeError):
            raise serializers.ValidationError("Invalid offset.") from None
        providers = catalog.get("providers", {})
        rows = providers.get(provider, {}) if provider else providers
        ids = sorted(key for key in rows if query.casefold() in key.casefold())
        results = [{"id": key, **({"capacity": rows[key]} if provider else {"model_count": len(rows[key])})}
                   for key in ids[offset:offset + 50]]
        return Response({"results": results, "total": len(ids), "next_offset": offset + 50 if offset + 50 < len(ids) else None,
                         "updated_at": catalog.get("updated_at"), "available": bool(providers)},
                        headers={"Cache-Control": "private, no-store"})
