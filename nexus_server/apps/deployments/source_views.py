"""Shared Source/Pool operations; host-selected inputs and response policy."""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from .presentation import response_context
from .serializers import (DeploymentSerializer, DeploymentCreateSerializer, ModelGroupDeploymentSerializer,
    ModelGroupRoutingSerializer, ModelGroupSerializer, ModelGroupSourceRoutingSerializer, ModelSourceBatchSerializer)
from .services import (create_deployment, create_model_sources_batch, delete_deployment, deployment_status,
    disable_deployment, list_deployments, list_model_group_routing_history, list_visible_models,
    preview_model_group_routing, rollback_model_group_routing, run_deployment_health_check,
    set_pricing, set_visibility, update_deployment, update_model_group_routing,
    update_model_group_source_routing, ManualSourceCreationDisabled)

class DeploymentListCreateView(APIView):
    def get(self, request):
        return Response(DeploymentSerializer(list_deployments(request=request).order_by("deployment_id"), many=True, context={"request": request}).data)

    def post(self, request):
        if (request.data.get("source_type") or "manual") == "manual":
            raise ManualSourceCreationDisabled()
        serializer = DeploymentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        deployment = create_deployment(request=request, data=serializer.validated_data)
        return Response(DeploymentSerializer(deployment, context={"request": request}).data, status=status.HTTP_201_CREATED)



class ModelSourceBatchView(APIView):
    def post(self, request):
        serializer = ModelSourceBatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        deployments = create_model_sources_batch(request=request, data=serializer.validated_data)
        return Response(DeploymentSerializer(deployments, many=True, context={"request": request}).data, status=status.HTTP_201_CREATED)



class DeploymentDetailView(APIView):
    def patch(self, request, deployment_id):
        deployment = update_deployment(
            request=request,
            deployment_identifier=deployment_id,
            data=dict(request.data),
        )
        return Response(DeploymentSerializer(deployment, **response_context(request)).data)

    def delete(self, request, deployment_id):
        deployment = delete_deployment(request=request, deployment_identifier=deployment_id)
        return Response(DeploymentSerializer(deployment, **response_context(request)).data)



class DeploymentDisableView(APIView):
    def post(self, request, deployment_id):
        deployment = disable_deployment(request=request, deployment_identifier=deployment_id)
        return Response(DeploymentSerializer(deployment, **response_context(request)).data)



class DeploymentVisibilityView(APIView):
    def post(self, request, deployment_id):
        deployment = set_visibility(
            request=request,
            deployment_identifier=deployment_id,
            visibility=str(request.data.get("visibility") or ""),
        )
        return Response(DeploymentSerializer(deployment, **response_context(request)).data)



class DeploymentPricingView(APIView):
    def post(self, request, deployment_id):
        deployment = set_pricing(
            request=request,
            deployment_identifier=deployment_id,
            pricing_rate=request.data.get("pricing_rate"),
        )
        return Response(DeploymentSerializer(deployment, **response_context(request)).data)



class DeploymentStatusView(APIView):
    def get(self, request):
        return Response(DeploymentSerializer(deployment_status(request=request), many=True, **response_context(request)).data)



class DeploymentHealthCheckView(APIView):
    def post(self, request, deployment_id):
        deployment = run_deployment_health_check(request=request, deployment_identifier=deployment_id)
        return Response(DeploymentSerializer(deployment, **response_context(request)).data)



class ModelListView(APIView):
    def get(self, request):
        return Response(ModelGroupSerializer(list_visible_models(request=request), many=True, context={"request": request}).data)



class ModelGroupRoutingView(APIView):
    def patch(self, request, model_group_id):
        serializer = ModelGroupRoutingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        group = update_model_group_routing(
            request=request,
            model_group_identifier=model_group_id,
            data=serializer.validated_data,
        )
        return Response(ModelGroupSerializer(group, context={"request": request}).data)



class ModelGroupRoutingPreviewView(APIView):
    def post(self, request, model_group_id):
        serializer = ModelGroupRoutingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(preview_model_group_routing(
            request=request,
            model_group_identifier=model_group_id,
            data=serializer.validated_data,
        ))



class ModelGroupRoutingHistoryView(APIView):
    def get(self, request, model_group_id):
        return Response(list_model_group_routing_history(
            request=request,
            model_group_identifier=model_group_id,
        ))



class ModelGroupRoutingRollbackView(APIView):
    def post(self, request, model_group_id, revision):
        expected_revision = request.data.get("expected_revision")
        if not isinstance(expected_revision, int) or expected_revision < 1:
            return Response({"code": "INVALID_EXPECTED_REVISION", "message": "expected_revision is required."}, status=status.HTTP_400_BAD_REQUEST)
        group = rollback_model_group_routing(
            request=request,
            model_group_identifier=model_group_id,
            expected_revision=expected_revision,
            target_revision=revision,
        )
        return Response(ModelGroupSerializer(group, context={"request": request}).data)



class ModelGroupSourceRoutingView(APIView):
    def patch(self, request, model_group_id, source_id):
        serializer = ModelGroupSourceRoutingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        link = update_model_group_source_routing(
            request=request,
            model_group_identifier=model_group_id,
            source_id=source_id,
            data=serializer.validated_data,
        )
        return Response(ModelGroupDeploymentSerializer(link, **response_context(request)).data)



