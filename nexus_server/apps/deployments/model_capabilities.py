from django.db import transaction
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.audit.services import log_audit
from apps.gateway import models_dev
from apps.gateway.model_capabilities import capabilities_payload, parse_update
from apps.gateway.model_info import source_fields
from .model_reference import source_access
from .models import Deployment


def payload(source, access):
    catalog = models_dev.snapshot()
    return capabilities_payload(override=source.model_capabilities_override,
        fields=source_fields(source, catalog), inherited=source_fields(source, catalog, include_override=False), access=access)


class SourceModelCapabilitiesView(APIView):
    def get(self, request, deployment_id):
        source, access = source_access(request, deployment_id, "can_read")
        return Response(payload(source, access), headers={"Cache-Control": "private, no-store"})

    @transaction.atomic
    def patch(self, request, deployment_id):
        source, access = source_access(request, deployment_id, "can_edit")
        value, expected = parse_update(request.data)
        source = Deployment.objects.select_for_update().get(pk=source.pk)
        before = source.model_capabilities_override
        if before != expected:
            return Response({"detail": "Capabilities changed. Reload before saving.", "code": "MODEL_CAPABILITIES_CONFLICT"}, status=409)
        source.model_capabilities_override = value
        source.save(update_fields=["model_capabilities_override", "updated_at"])
        log_audit(request=request, actor=request.user, action="deployments.model_capabilities.update", resource_type="deployment",
                  resource_id=str(source.pk), metadata={"before": before, "after": value})
        return Response(payload(source, access), headers={"Cache-Control": "private, no-store"})
