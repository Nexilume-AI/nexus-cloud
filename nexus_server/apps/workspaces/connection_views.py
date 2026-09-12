"""Unchanged Computer detail HTTP contract."""
from rest_framework.views import APIView
from rest_framework.response import Response
from .serializers import WorkspaceConnectionSerializer, WorkspaceConnectionUpdateSerializer
from .connection_core import get_workspace_connection, list_workspace_connections
from .connection_management import update_workspace_connection, delete_workspace_connection


class WorkspaceConnectionListView(APIView):
    def get(self, request):
        connections = list_workspace_connections(request=request)
        return Response(WorkspaceConnectionSerializer(connections, many=True).data)


class WorkspaceConnectionDetailView(APIView):
    def get(self, request, connection_id):
        return Response(WorkspaceConnectionSerializer(get_workspace_connection(request=request, connection_id=connection_id)).data)

    def patch(self, request, connection_id):
        serializer = WorkspaceConnectionUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        connection = update_workspace_connection(request=request, connection_id=connection_id, data=serializer.validated_data)
        return Response(WorkspaceConnectionSerializer(connection).data)

    def delete(self, request, connection_id):
        connection = delete_workspace_connection(request=request, connection_id=connection_id)
        return Response(WorkspaceConnectionSerializer(connection).data)
