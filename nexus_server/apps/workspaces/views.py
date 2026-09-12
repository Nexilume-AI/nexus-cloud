from __future__ import annotations
from .connection_views import WorkspaceConnectionDetailView, WorkspaceConnectionListView
from .tool_views import (
    WorkspaceTerminalSessionToolConfigView,
    WorkspaceTerminalSessionToolConfigApplyView,
    WorkspaceTerminalSessionToolConfigOptionsView,
    WorkspaceTerminalSessionToolConfigPreviewView,
    WorkspaceTerminalSessionToolConfigApplyV2View,
    WorkspaceTerminalSessionToolConfigRollbackView,
)
from .terminal_views import (
    WorkspaceTerminalSessionListCreateView,
    WorkspaceTerminalSessionDetailView,
    WorkspaceTerminalSessionCloseView,
    WorkspaceTerminalSessionTicketView,
    WorkspaceTerminalSessionToolStatusView,
)
from .computer_views import (
    ComputerListView, ComputerPairingCodeView, ComputerRevokeView,
    ComputerRuntimeEnrollView, ComputerRuntimeSessionView,
    ComputerRuntimeCommandUploadView, ComputerRuntimeUnpairView,
)

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import (
    WorkspaceConnectionCreateSerializer,
    WorkspaceConnectionSerializer,
    WorkspaceConnectionTestSerializer,
    WorkspaceConnectionUpdateSerializer,
    WorkspaceConnectionValidateSerializer,
    ComputerRuntimeEnrollSerializer,
    ComputerRuntimePairingCreateSerializer,
    ComputerRuntimeSessionSerializer,
    ComputerRuntimeUnpairSerializer,
    WorkspaceToolConfigApplySerializer,
    WorkspaceToolConfigChangeSerializer,
    WorkspaceTerminalSessionCreateSerializer,
    WorkspaceTerminalSessionSerializer,
)
from .computer_runtime import (
    create_pairing_code,
    create_runtime_session,
    enroll_runtime,
    list_computers,
    revoke_computer,
    store_runtime_upload,
    unpair_runtime,
)
from .services import (
    WorkspaceError,
    apply_workspace_tool_config,
    apply_workspace_tool_config_change,
    close_terminal_session,
    create_terminal_session,
    create_workspace_connection,
    delete_workspace_connection,
    get_terminal_session,
    get_workspace_tool_config,
    get_workspace_tool_config_options,
    get_workspace_connection,
    list_terminal_sessions,
    list_workspace_connections,
    issue_terminal_websocket_ticket,
    refresh_terminal_tool_status,
    preview_workspace_tool_config,
    rollback_workspace_tool_config,
    test_workspace_connection,
    update_workspace_connection,
    validate_workspace_connection,
)


class WorkspaceConnectionListCreateView(WorkspaceConnectionListView):
    def post(self, request):
        from .computer_runtime import LegacySSHDisabled

        if not bool(getattr(settings, "NEXUS_LEGACY_SSH_ENABLED", False)):
            raise LegacySSHDisabled("Use POST /api/v1/computers/pairing-codes/ to add a Computer.")
        serializer = WorkspaceConnectionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        connection = create_workspace_connection(request=request, data=serializer.validated_data)
        return Response(WorkspaceConnectionSerializer(connection).data, status=status.HTTP_201_CREATED)




class WorkspaceConnectionTestView(APIView):
    def post(self, request, connection_id):
        result = test_workspace_connection(request=request, connection_id=connection_id)
        return Response(WorkspaceConnectionTestSerializer(result).data)


class WorkspaceConnectionValidateView(APIView):
    def post(self, request):
        from .computer_runtime import LegacySSHDisabled

        if not bool(getattr(settings, "NEXUS_LEGACY_SSH_ENABLED", False)):
            raise LegacySSHDisabled()
        serializer = WorkspaceConnectionValidateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(validate_workspace_connection(request=request, data=serializer.validated_data))
