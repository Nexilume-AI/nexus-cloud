"""Shared Tool Setup HTTP contract with explicit host orchestration."""
from rest_framework.response import Response
from rest_framework.views import APIView
from .connection_core import WorkspaceError
from .serializers import WorkspaceToolConfigApplySerializer, WorkspaceToolConfigChangeSerializer
from .tool_setup_services import (
    get_workspace_tool_config,
    get_workspace_tool_config_options,
    preview_workspace_tool_config,
    apply_workspace_tool_config_change,
    apply_workspace_tool_config,
    rollback_workspace_tool_config,
)


class WorkspaceTerminalSessionToolConfigView(APIView):
    def get(self, request, session_id):
        return Response(get_workspace_tool_config(request=request, session_id=session_id, tool=request.query_params.get("tool") or "codex"))


class WorkspaceTerminalSessionToolConfigApplyView(APIView):
    def post(self, request, session_id):
        if "section" in request.data or "action" in request.data:
            serializer = WorkspaceToolConfigChangeSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            if not serializer.validated_data.get("expected_revision"):
                raise WorkspaceError("expected_revision is required when applying Tool setup changes.")
            return Response(apply_workspace_tool_config_change(request=request, session_id=session_id, data=serializer.validated_data))
        serializer = WorkspaceToolConfigApplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(apply_workspace_tool_config(request=request, session_id=session_id, data=serializer.validated_data))


class WorkspaceTerminalSessionToolConfigOptionsView(APIView):
    def get(self, request, session_id):
        return Response(get_workspace_tool_config_options(request=request, session_id=session_id, tool=request.query_params.get("tool") or "codex"))


class WorkspaceTerminalSessionToolConfigPreviewView(APIView):
    def post(self, request, session_id):
        serializer = WorkspaceToolConfigChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(preview_workspace_tool_config(request=request, session_id=session_id, data=serializer.validated_data))


class WorkspaceTerminalSessionToolConfigApplyV2View(APIView):
    def post(self, request, session_id):
        serializer = WorkspaceToolConfigChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not serializer.validated_data.get("expected_revision"):
            raise WorkspaceError("expected_revision is required when applying Tool setup changes.")
        return Response(apply_workspace_tool_config_change(request=request, session_id=session_id, data=serializer.validated_data))


class WorkspaceTerminalSessionToolConfigRollbackView(APIView):
    def post(self, request, session_id):
        return Response(rollback_workspace_tool_config(request=request, session_id=session_id, tool=request.data.get("tool") or "codex"))
