from __future__ import annotations

from rest_framework import status
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import (
    CanonicalModelSerializer,
    CanonicalModelMergeSerializer,
    DeploymentSerializer,
    DeploymentCreateSerializer,
    ModelGroupDeploymentSerializer,
    ModelGroupRoutingSerializer,
    ModelGroupSerializer,
    ModelGroupSourceRoutingSerializer,
    ModelSourceBatchSerializer,
)
from .services import (
    create_deployment,
    create_model_sources_batch,
    delete_deployment,
    deployment_status,
    disable_deployment,
    list_deployments,
    list_model_group_routing_history,
    list_visible_models,
    preview_model_group_routing,
    rollback_model_group_routing,
    run_deployment_health_check,
    set_pricing,
    set_visibility,
    update_deployment,
    update_model_group_routing,
    update_model_group_source_routing,
    ManualSourceCreationDisabled,
)
from .models import CanonicalModel, Deployment, ModelGroup
from .topology import topology_payload


from .source_views import DeploymentListCreateView


from .source_views import ModelSourceBatchView


class CanonicalModelListCreateView(APIView):
    def get(self, request):
        models = CanonicalModel.objects.exclude(status="deleted").order_by("key")
        return Response(CanonicalModelSerializer(models, many=True).data)

    def post(self, request):
        if not request.user.is_superuser:
            return Response({"detail": "Super administrator permission is required."}, status=status.HTTP_403_FORBIDDEN)
        serializer = CanonicalModelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        model = serializer.save()
        return Response(CanonicalModelSerializer(model).data, status=status.HTTP_201_CREATED)


class CanonicalModelDetailView(APIView):
    def patch(self, request, model_id):
        if not request.user.is_superuser:
            return Response({"detail": "Super administrator permission is required."}, status=status.HTTP_403_FORBIDDEN)
        model = CanonicalModel.objects.exclude(status="deleted").filter(id=model_id).first()
        if model is None:
            return Response({"detail": "Canonical Model not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = CanonicalModelSerializer(model, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        model = serializer.save()
        return Response(CanonicalModelSerializer(model).data)


class CanonicalModelMergeView(APIView):
    def post(self, request, model_id):
        if not request.user.is_superuser:
            return Response({"detail": "Super administrator permission is required."}, status=status.HTTP_403_FORBIDDEN)
        serializer = CanonicalModelMergeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        from django.db import transaction
        from rest_framework import exceptions
        from apps.common.models import SoftDeleteModel
        from apps.providers.models import ProviderRuntimeModelOffer

        with transaction.atomic():
            source = CanonicalModel.objects.select_for_update().exclude(status=SoftDeleteModel.STATUS_DELETED).filter(id=model_id).first()
            target = CanonicalModel.objects.select_for_update().filter(
                id=serializer.validated_data["target_model_id"],
                status=CanonicalModel.STATUS_ACTIVE,
            ).first()
            if source is None or target is None:
                raise exceptions.NotFound("Canonical Model not found.")
            if source.id == target.id:
                raise exceptions.ValidationError("Choose a different target Canonical Model.")
            source_runtime_ids = set(
                ProviderRuntimeModelOffer.objects.exclude(status=SoftDeleteModel.STATUS_DELETED)
                .filter(canonical_model=source)
                .values_list("runtime_account_id", flat=True)
            )
            conflict = ProviderRuntimeModelOffer.objects.exclude(status=SoftDeleteModel.STATUS_DELETED).filter(
                canonical_model=target,
                runtime_account_id__in=source_runtime_ids,
            ).exists()
            if conflict:
                raise exceptions.ValidationError(
                    "Merge blocked because a Provider Runtime has Offers mapped to both Canonical Models."
                )
            Deployment.objects.filter(canonical_model=source).update(canonical_model=target)
            ModelGroup.objects.filter(canonical_model=source).update(canonical_model=target)
            ProviderRuntimeModelOffer.objects.filter(canonical_model=source).update(canonical_model=target)
            source.status = SoftDeleteModel.STATUS_DELETED
            source.deleted_at = timezone.now()
            source.metadata = {**(source.metadata or {}), "merged_into": str(target.id)}
            source.save(update_fields=["status", "deleted_at", "metadata", "updated_at"])
        return Response(CanonicalModelSerializer(target).data)


from .source_views import DeploymentDetailView


from .source_views import DeploymentDisableView


from .source_views import DeploymentVisibilityView


from .source_views import DeploymentPricingView


from .source_views import DeploymentStatusView


from .source_views import DeploymentHealthCheckView


from .source_views import ModelListView


from .source_views import ModelGroupRoutingView


from .source_views import ModelGroupRoutingPreviewView


from .source_views import ModelGroupRoutingHistoryView


from .source_views import ModelGroupRoutingRollbackView


from .source_views import ModelGroupSourceRoutingView


from .topology_views import TopologyView
