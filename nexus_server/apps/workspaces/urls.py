from __future__ import annotations

from django.urls import path

from .views import (
    ComputerListView,
    ComputerPairingCodeView,
    ComputerRevokeView,
    ComputerRuntimeEnrollView,
    ComputerRuntimeCommandUploadView,
    ComputerRuntimeSessionView,
    ComputerRuntimeUnpairView,
    WorkspaceConnectionDetailView,
    WorkspaceConnectionListCreateView,
    WorkspaceConnectionTestView,
    WorkspaceConnectionValidateView,
    WorkspaceTerminalSessionCloseView,
    WorkspaceTerminalSessionDetailView,
    WorkspaceTerminalSessionListCreateView,
    WorkspaceTerminalSessionTicketView,
    WorkspaceTerminalSessionToolConfigApplyView,
    WorkspaceTerminalSessionToolConfigApplyV2View,
    WorkspaceTerminalSessionToolConfigOptionsView,
    WorkspaceTerminalSessionToolConfigPreviewView,
    WorkspaceTerminalSessionToolConfigRollbackView,
    WorkspaceTerminalSessionToolConfigView,
    WorkspaceTerminalSessionToolStatusView,
)


urlpatterns = [
    path("computers/", ComputerListView.as_view(), name="computers"),
    path("computers/pairing-codes/", ComputerPairingCodeView.as_view(), name="computer-pairing-codes"),
    path("computers/<uuid:connection_id>/revoke/", ComputerRevokeView.as_view(), name="computer-revoke"),
    path("computer-runtime/v1/enroll/", ComputerRuntimeEnrollView.as_view(), name="computer-runtime-enroll"),
    path("computer-runtime/v1/sessions/", ComputerRuntimeSessionView.as_view(), name="computer-runtime-session"),
    path("computer-runtime/v1/unpair/", ComputerRuntimeUnpairView.as_view(), name="computer-runtime-unpair"),
    path("computer-runtime/v1/commands/<uuid:command_id>/upload/", ComputerRuntimeCommandUploadView.as_view(), name="computer-runtime-command-upload"),
    path("workspace-connections/", WorkspaceConnectionListCreateView.as_view(), name="workspace-connections"),
    path("workspace-connections/validate/", WorkspaceConnectionValidateView.as_view(), name="workspace-connection-validate"),
    path("workspace-connections/<uuid:connection_id>/", WorkspaceConnectionDetailView.as_view(), name="workspace-connection-detail"),
    path("workspace-connections/<uuid:connection_id>/test/", WorkspaceConnectionTestView.as_view(), name="workspace-connection-test"),
    path("workspace-terminal-sessions/", WorkspaceTerminalSessionListCreateView.as_view(), name="workspace-terminal-sessions"),
    path("workspace-terminal-sessions/<uuid:session_id>/", WorkspaceTerminalSessionDetailView.as_view(), name="workspace-terminal-session-detail"),
    path("workspace-terminal-sessions/<uuid:session_id>/close/", WorkspaceTerminalSessionCloseView.as_view(), name="workspace-terminal-session-close"),
    path("workspace-terminal-sessions/<uuid:session_id>/ticket/", WorkspaceTerminalSessionTicketView.as_view(), name="workspace-terminal-session-ticket"),
    path("workspace-terminal-sessions/<uuid:session_id>/tool-status/", WorkspaceTerminalSessionToolStatusView.as_view(), name="workspace-terminal-session-tool-status"),
    path("workspace-terminal-sessions/<uuid:session_id>/tool-config/", WorkspaceTerminalSessionToolConfigView.as_view(), name="workspace-terminal-session-tool-config"),
    path("workspace-terminal-sessions/<uuid:session_id>/tool-config/apply/", WorkspaceTerminalSessionToolConfigApplyView.as_view(), name="workspace-terminal-session-tool-config-apply"),
    path("workspace-terminal-sessions/<uuid:session_id>/tool-config/options/", WorkspaceTerminalSessionToolConfigOptionsView.as_view(), name="workspace-terminal-session-tool-config-options"),
    path("workspace-terminal-sessions/<uuid:session_id>/tool-config/preview/", WorkspaceTerminalSessionToolConfigPreviewView.as_view(), name="workspace-terminal-session-tool-config-preview"),
    path("workspace-terminal-sessions/<uuid:session_id>/tool-config/apply-v2/", WorkspaceTerminalSessionToolConfigApplyV2View.as_view(), name="workspace-terminal-session-tool-config-apply-v2"),
    path("workspace-terminal-sessions/<uuid:session_id>/tool-config/rollback/", WorkspaceTerminalSessionToolConfigRollbackView.as_view(), name="workspace-terminal-session-tool-config-rollback"),
]
