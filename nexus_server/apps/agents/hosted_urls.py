"""Hosted image/deployment management; excludes commercial billing callbacks."""
from django.urls import path
from .python_build_views import AgentPythonBuildsView, AgentPythonBuildDetailView, AgentPythonBuildDeleteFailedView
from .hosted_views import (
    AgentRuntimeImageListCreateView,
    AgentRuntimeImageSetCurrentView,
    AgentRuntimeImageDetailView,
    AgentRuntimeDeploymentView,
    AgentRuntimeStopView,
    AgentRuntimeStatusView,
    AgentRuntimeHealthCheckView,
    AgentRuntimeMCPExportView,
)

urlpatterns = [
    path("agents/<uuid:agent_id>/runtime/python-builds/", AgentPythonBuildsView.as_view(), name="agent-python-builds"),
    path("agents/<uuid:agent_id>/runtime/python-builds/delete-failed/", AgentPythonBuildDeleteFailedView.as_view(), name="agent-python-build-delete-failed"),
    path("agents/<uuid:agent_id>/runtime/python-builds/<uuid:build_id>/", AgentPythonBuildDetailView.as_view(), name="agent-python-build-detail"),
    path("agents/<uuid:agent_id>/runtime/images/", AgentRuntimeImageListCreateView.as_view(), name="agent-runtime-images"),
    path("agents/<uuid:agent_id>/runtime/images/<uuid:image_id>/", AgentRuntimeImageDetailView.as_view(), name="agent-runtime-image-detail"),
    path("agents/<uuid:agent_id>/runtime/images/<uuid:image_id>/set-current/", AgentRuntimeImageSetCurrentView.as_view(), name="agent-runtime-image-set-current"),
    path("agents/<uuid:agent_id>/runtime/deployments/", AgentRuntimeDeploymentView.as_view(), name="agent-runtime-deployments"),
    path("agents/<uuid:agent_id>/runtime/deployments/stop/", AgentRuntimeStopView.as_view(), name="agent-runtime-stop"),
    path("agents/<uuid:agent_id>/runtime/status/", AgentRuntimeStatusView.as_view(), name="agent-runtime-status"),
    path("agents/<uuid:agent_id>/runtime/health-check/", AgentRuntimeHealthCheckView.as_view(), name="agent-runtime-health-check"),
    path("agents/<uuid:agent_id>/runtime/mcp/export/", AgentRuntimeMCPExportView.as_view(), name="agent-runtime-mcp-export"),
]
