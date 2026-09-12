"""Hosted Agent management endpoints, shared without commercial composition."""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from .runtime_serializers import (
    AgentRuntimeDeploySerializer, AgentRuntimeDeploymentSerializer,
    AgentRuntimeImageCreateSerializer, AgentRuntimeImageSerializer, AgentRuntimeStopSerializer,
)
from .runtime_services import (
    list_runtime_images, register_runtime_image, set_current_runtime_image,
    enqueue_deploy_runtime, enqueue_stop_runtime, runtime_status,
    enqueue_runtime_health_check, export_runtime_mcp,
)


class AgentRuntimeImageListCreateView(APIView):
    def get(self, request, agent_id):
        return Response(AgentRuntimeImageSerializer(list_runtime_images(request=request, agent_id=agent_id), many=True).data)

    def post(self, request, agent_id):
        serializer = AgentRuntimeImageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        uploaded_file = request.FILES.get("file") or request.FILES.get("image_file")
        image = register_runtime_image(
            request=request,
            agent_id=agent_id,
            data=serializer.validated_data,
            uploaded_file=uploaded_file,
        )
        return Response(AgentRuntimeImageSerializer(image).data, status=status.HTTP_201_CREATED)


class AgentRuntimeImageSetCurrentView(APIView):
    def post(self, request, agent_id, image_id):
        image = set_current_runtime_image(request=request, agent_id=agent_id, image_id=image_id)
        return Response(AgentRuntimeImageSerializer(image).data)


class AgentRuntimeImageDetailView(APIView):
    def delete(self, request, agent_id, image_id):
        from .runtime_images import delete_runtime_image
        response = Response(delete_runtime_image(request=request, agent_id=agent_id, image_id=image_id))
        response["Cache-Control"] = "private, no-store"
        return response


class AgentRuntimeDeploymentView(APIView):
    def post(self, request, agent_id):
        serializer = AgentRuntimeDeploySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        deployment, job = enqueue_deploy_runtime(
            request=request,
            agent_id=agent_id,
            image_id=serializer.validated_data.get("image_id"),
            env=serializer.validated_data["env"],
            workspace_connection_id=serializer.validated_data.get("workspace_connection_id"),
            workspace_root=serializer.validated_data.get("workspace_root", ""),
            workspace_access_mode=serializer.validated_data.get("workspace_access_mode"),
        )
        data = AgentRuntimeDeploymentSerializer(deployment).data
        data["job_id"] = str(job.id)
        return Response(data, status=status.HTTP_201_CREATED)


class AgentRuntimeStopView(APIView):
    def post(self, request, agent_id):
        serializer = AgentRuntimeStopSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        deployment, job = enqueue_stop_runtime(request=request, agent_id=agent_id, env=serializer.validated_data["env"])
        data = AgentRuntimeDeploymentSerializer(deployment).data
        data["job_id"] = str(job.id)
        return Response(data)


class AgentRuntimeStatusView(APIView):
    def get(self, request, agent_id):
        result = runtime_status(request=request, agent_id=agent_id)
        return Response(
            {
                "images": AgentRuntimeImageSerializer(result["images"], many=True).data,
                "deployments": AgentRuntimeDeploymentSerializer(result["deployments"], many=True).data,
            }
        )


class AgentRuntimeHealthCheckView(APIView):
    def post(self, request, agent_id):
        serializer = AgentRuntimeStopSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        deployment, job = enqueue_runtime_health_check(request=request, agent_id=agent_id, env=serializer.validated_data["env"])
        data = AgentRuntimeDeploymentSerializer(deployment).data
        data["job_id"] = str(job.id)
        return Response(data)


class AgentRuntimeMCPExportView(APIView):
    def get(self, request, agent_id):
        return Response(export_runtime_mcp(request=request, agent_id=agent_id))
