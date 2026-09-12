"""Source/Pool operations and topology over the installed Router schema."""
from django.urls import path
from .topology_views import TopologyView
from .source_views import (
    DeploymentListCreateView,
    ModelSourceBatchView,
    DeploymentDetailView,
    DeploymentDisableView,
    DeploymentVisibilityView,
    DeploymentPricingView,
    DeploymentStatusView,
    DeploymentHealthCheckView,
    ModelListView,
    ModelGroupRoutingView,
    ModelGroupRoutingPreviewView,
    ModelGroupRoutingHistoryView,
    ModelGroupRoutingRollbackView,
    ModelGroupSourceRoutingView,
)

urlpatterns = [
    path("topology/", TopologyView.as_view(), name="topology"),
    path("deployments/", DeploymentListCreateView.as_view(), name="deployments"),
    path("model-sources/batch/", ModelSourceBatchView.as_view(), name="model-sources-batch"),
    path("deployments/status/", DeploymentStatusView.as_view(), name="deployment-status"),
    path("deployments/<str:deployment_id>/", DeploymentDetailView.as_view(), name="deployment-detail"),
    path("deployments/<str:deployment_id>/health-check/", DeploymentHealthCheckView.as_view(), name="deployment-health-check"),
    path("deployments/<str:deployment_id>/disable/", DeploymentDisableView.as_view(), name="deployment-disable"),
    path("deployments/<str:deployment_id>/visibility/", DeploymentVisibilityView.as_view(), name="deployment-visibility"),
    path("deployments/<str:deployment_id>/pricing/", DeploymentPricingView.as_view(), name="deployment-pricing"),
    path("models/", ModelListView.as_view(), name="models"),
    path("models/<str:model_group_id>/routing/", ModelGroupRoutingView.as_view(), name="model-routing"),
    path("models/<str:model_group_id>/routing/preview/", ModelGroupRoutingPreviewView.as_view(), name="model-routing-preview"),
    path("models/<str:model_group_id>/routing/history/", ModelGroupRoutingHistoryView.as_view(), name="model-routing-history"),
    path("models/<str:model_group_id>/routing/history/<int:revision>/rollback/", ModelGroupRoutingRollbackView.as_view(), name="model-routing-rollback"),
    path("models/<str:model_group_id>/sources/<uuid:source_id>/", ModelGroupSourceRoutingView.as_view(), name="model-source-routing"),
]
