"""Owned Agent catalog routes shared by both distributions."""
from django.urls import path
from .catalog_views import (
    AgentCapabilitiesView,
    AgentCloneView,
    AgentDetailView,
    AgentListCreateView,
    AgentMemoryView,
)


urlpatterns = [
    path("agents/", AgentListCreateView.as_view(), name="agents"),
    path("agents/capabilities/", AgentCapabilitiesView.as_view(), name="agent-capabilities"),
    path("agents/<uuid:agent_id>/", AgentDetailView.as_view(), name="agent-detail"),
    path("agents/<uuid:agent_id>/clone/", AgentCloneView.as_view(), name="agent-clone"),
    path("agents/<uuid:agent_id>/memory/", AgentMemoryView.as_view(), name="agent-memory"),
]
