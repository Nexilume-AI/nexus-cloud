"""Owned Agent version and resource configuration, without commercial routes."""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from .serializers import (
    AgentSerializer, AgentVersionSerializer, AgentVersionPublishSerializer,
    AgentResourceSetSerializer, AgentResourceConfigSerializer,
)
from .services import repo_init, repo_push, list_versions, publish_version, rollback_version, set_resources


class AgentRepoInitView(APIView):
    def post(self, request, agent_id):
        return Response(AgentSerializer(repo_init(request=request, agent_id=agent_id), context={"request": request}).data)


class AgentRepoPushView(APIView):
    def post(self, request, agent_id):
        uploaded_file = request.FILES.get("file")
        commit_id = request.data.get("commit_id", "")
        return Response(AgentSerializer(repo_push(request=request, agent_id=agent_id, uploaded_file=uploaded_file, commit_id=commit_id), context={"request": request}).data)


class AgentVersionListView(APIView):
    def get(self, request, agent_id):
        return Response(AgentVersionSerializer(list_versions(request=request, agent_id=agent_id), many=True).data)


class AgentVersionPublishView(APIView):
    def post(self, request, agent_id):
        serializer = AgentVersionPublishSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        version = publish_version(
            request=request,
            agent_id=agent_id,
            release_notes=serializer.validated_data["release_notes"],
        )
        return Response(AgentVersionSerializer(version).data, status=status.HTTP_201_CREATED)


class AgentVersionRollbackView(APIView):
    def post(self, request, agent_id, version):
        return Response(AgentVersionSerializer(rollback_version(request=request, agent_id=agent_id, version=version)).data)


class AgentResourcesView(APIView):
    def post(self, request, agent_id):
        serializer = AgentResourceSetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        config = set_resources(
            request=request,
            agent_id=agent_id,
            cpu=serializer.validated_data["cpu"],
            memory=serializer.validated_data["memory"],
        )
        return Response(AgentResourceConfigSerializer(config).data)
