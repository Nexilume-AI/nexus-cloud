from django.urls import path
from .terminal_views import (
    WorkspaceTerminalSessionListCreateView, WorkspaceTerminalSessionDetailView,
    WorkspaceTerminalSessionCloseView, WorkspaceTerminalSessionTicketView,
    WorkspaceTerminalSessionToolStatusView,
)

urlpatterns = [
    path("workspace-terminal-sessions/", WorkspaceTerminalSessionListCreateView.as_view(), name="workspace-terminal-sessions"),
    path("workspace-terminal-sessions/<uuid:session_id>/", WorkspaceTerminalSessionDetailView.as_view(), name="workspace-terminal-session-detail"),
    path("workspace-terminal-sessions/<uuid:session_id>/close/", WorkspaceTerminalSessionCloseView.as_view(), name="workspace-terminal-session-close"),
    path("workspace-terminal-sessions/<uuid:session_id>/ticket/", WorkspaceTerminalSessionTicketView.as_view(), name="workspace-terminal-session-ticket"),
    path("workspace-terminal-sessions/<uuid:session_id>/tool-status/", WorkspaceTerminalSessionToolStatusView.as_view(), name="workspace-terminal-session-tool-status"),
]
