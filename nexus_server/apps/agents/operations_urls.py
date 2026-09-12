"""Owned operational HTTP routes shared by both distributions."""
from django.urls import path
from .operations_views import (
    AgentMCPExportView,
    AgentLogsView,
    AgentStatusView,
    AgentDisplayRunView,
    AgentDisplayRunEventsView,
    AgentDisplayRunRedactView,
    AgentOutputArtifactView,
    AgentOutputArtifactScanView,
)

urlpatterns = [
    path("agents/<uuid:agent_id>/mcp/export/", AgentMCPExportView.as_view(), name="agent-mcp-export"),
    path("agents/<uuid:agent_id>/logs/", AgentLogsView.as_view(), name="agent-logs"),
    path("agents/<uuid:agent_id>/status/", AgentStatusView.as_view(), name="agent-status"),
    path("agents/<uuid:agent_id>/display-runs/", AgentDisplayRunView.as_view(), name="agent-display-runs"),
    path("agents/<uuid:agent_id>/display-runs/<uuid:run_id>/events/", AgentDisplayRunEventsView.as_view(), name="agent-display-run-events"),
    path("agents/<uuid:agent_id>/display-runs/<uuid:run_id>/redact/", AgentDisplayRunRedactView.as_view(), name="agent-display-run-redact"),
    path("agents/<uuid:agent_id>/display-runs/<uuid:run_id>/outputs/", AgentOutputArtifactView.as_view(), name="agent-output-artifacts"),
    path("agents/<uuid:agent_id>/display-runs/<uuid:run_id>/outputs/<uuid:artifact_id>/scan/", AgentOutputArtifactScanView.as_view(), name="agent-output-artifact-scan"),
]
