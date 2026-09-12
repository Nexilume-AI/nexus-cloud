"""Shared bounded Agent operational diagnostics routes."""
from django.urls import path
from .observability import AgentObservabilityView

urlpatterns = [
    path("agents/<uuid:agent_id>/observability/runs/", AgentObservabilityView.as_view(), name="agent-observability-runs"),
    path("agents/<uuid:agent_id>/observability/memory/", AgentObservabilityView.as_view(), {"kind": "memory"}, name="agent-observability-memory"),
    path("agents/<uuid:agent_id>/observability/logs/", AgentObservabilityView.as_view(), {"kind": "logs"}, name="agent-observability-logs"),
    path("agents/<uuid:agent_id>/observability/runs/<uuid:run_id>/events/", AgentObservabilityView.as_view(), {"kind": "events"}, name="agent-observability-events"),
    path("agents/<uuid:agent_id>/observability/runs/<uuid:run_id>/outputs/", AgentObservabilityView.as_view(), {"kind": "outputs"}, name="agent-observability-outputs"),
]
