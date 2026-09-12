"""Caller device HTTP routes shared by both distributions."""
from django.urls import path
from .device_views import (
    AgentComputerBindingView,
    AgentComputerBindingDetailView,
    AgentWorkspaceGrantView,
    AgentMobileBindingView,
    AgentMobileBindingDetailView,
    AgentMobileGrantView,
    PrivateAgentRunComputerView,
    PrivateAgentRunTerminalTicketView,
)

urlpatterns = [
    path("agent-runs/<uuid:run_id>/computer/", PrivateAgentRunComputerView.as_view(), name="private-agent-run-computer"),
    path("agent-runs/<uuid:run_id>/terminal-ticket/", PrivateAgentRunTerminalTicketView.as_view(), name="private-agent-run-terminal-ticket"),
    path("agents/<uuid:agent_id>/computer-bindings/", AgentComputerBindingView.as_view(), name="agent-computer-bindings"),
    path("agents/<uuid:agent_id>/computer-bindings/<uuid:binding_id>/", AgentComputerBindingDetailView.as_view(), name="agent-computer-binding-detail"),
    path("agents/<uuid:agent_id>/workspace-grant/", AgentWorkspaceGrantView.as_view(), name="agent-workspace-grant"),
    path("agents/<uuid:agent_id>/mobile-bindings/", AgentMobileBindingView.as_view(), name="agent-mobile-bindings"),
    path("agents/<uuid:agent_id>/mobile-bindings/<uuid:binding_id>/", AgentMobileBindingDetailView.as_view(), name="agent-mobile-binding-detail"),
    path("agents/<uuid:agent_id>/mobile-grant/", AgentMobileGrantView.as_view(), name="agent-mobile-grant"),
]
