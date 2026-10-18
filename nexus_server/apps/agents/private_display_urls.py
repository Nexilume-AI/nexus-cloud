"""Shared caller-only Run presentation URLs."""
from django.urls import path
from .credential_views import CredentialAuthorizationView, authorization_page
from .private_display_views import (
    PrivateAgentRunDisplayView,
    PrivateAgentRunEventsView,
    PrivateAgentRunOutputsView,
    PrivateAgentRunOutputDownloadView,
    PrivateAgentRunTerminalView,
    PrivateAgentRunInteractionView,
    PrivateAgentRunDisplayAssetView
)
from .display_token_views import PrivateAgentRunDisplayTokenView
from .run_history import PrivateRunReadView

urlpatterns = [
    path("agent-authorizations/<uuid:authorization_id>/page/", authorization_page),
    path("agent-authorizations/<uuid:authorization_id>/", CredentialAuthorizationView.as_view()),
    path("agent-runs/<uuid:run_id>/read/", PrivateRunReadView.as_view(), name="private-agent-run-read"),
    path("agent-runs/<uuid:run_id>/display/", PrivateAgentRunDisplayView.as_view(), name="private-agent-run-display"),
    path("agent-runs/<uuid:run_id>/events/", PrivateAgentRunEventsView.as_view(), name="private-agent-run-events"),
    path("agent-runs/<uuid:run_id>/outputs/", PrivateAgentRunOutputsView.as_view(), name="private-agent-run-outputs"),
    path("agent-runs/<uuid:run_id>/outputs/<uuid:artifact_id>/download/", PrivateAgentRunOutputDownloadView.as_view(), name="private-agent-run-output-download"),
    path("agent-runs/<uuid:run_id>/terminal/", PrivateAgentRunTerminalView.as_view(), name="private-agent-run-terminal"),
    path("agent-runs/<uuid:run_id>/display-token/", PrivateAgentRunDisplayTokenView.as_view(), name="private-agent-run-display-token"),
    path("agent-runs/<uuid:run_id>/interactions/<uuid:interaction_id>/reply/", PrivateAgentRunInteractionView.as_view(), name="private-agent-run-interaction-reply"),
    path("agent-runs/<uuid:run_id>/display-assets/<uuid:asset_id>/", PrivateAgentRunDisplayAssetView.as_view(), name="private-agent-run-display-asset"),
]
