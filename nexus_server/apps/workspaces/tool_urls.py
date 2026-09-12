"""Original Tool Setup URLs, reusable without legacy SSH or private services."""
from django.urls import path
from . import tool_views

urlpatterns = [
    path("workspace-terminal-sessions/<uuid:session_id>/tool-config/", tool_views.WorkspaceTerminalSessionToolConfigView.as_view(), name="workspace-terminal-session-tool-config"),
    path("workspace-terminal-sessions/<uuid:session_id>/tool-config/apply/", tool_views.WorkspaceTerminalSessionToolConfigApplyView.as_view(), name="workspace-terminal-session-tool-config-apply"),
    path("workspace-terminal-sessions/<uuid:session_id>/tool-config/options/", tool_views.WorkspaceTerminalSessionToolConfigOptionsView.as_view(), name="workspace-terminal-session-tool-config-options"),
    path("workspace-terminal-sessions/<uuid:session_id>/tool-config/preview/", tool_views.WorkspaceTerminalSessionToolConfigPreviewView.as_view(), name="workspace-terminal-session-tool-config-preview"),
    path("workspace-terminal-sessions/<uuid:session_id>/tool-config/apply-v2/", tool_views.WorkspaceTerminalSessionToolConfigApplyV2View.as_view(), name="workspace-terminal-session-tool-config-apply-v2"),
    path("workspace-terminal-sessions/<uuid:session_id>/tool-config/rollback/", tool_views.WorkspaceTerminalSessionToolConfigRollbackView.as_view(), name="workspace-terminal-session-tool-config-rollback"),
]
