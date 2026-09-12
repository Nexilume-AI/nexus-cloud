"""Agent configuration routes shared by both distributions."""
from django.urls import path
from .configuration_views import (
    AgentRepoInitView,
    AgentRepoPushView,
    AgentVersionListView,
    AgentVersionPublishView,
    AgentVersionRollbackView,
    AgentResourcesView,
)

urlpatterns = [
    path("agents/<uuid:agent_id>/repo/init/", AgentRepoInitView.as_view(), name="agent-repo-init"),
    path("agents/<uuid:agent_id>/repo/push/", AgentRepoPushView.as_view(), name="agent-repo-push"),
    path("agents/<uuid:agent_id>/versions/", AgentVersionListView.as_view(), name="agent-versions"),
    path("agents/<uuid:agent_id>/versions/publish/", AgentVersionPublishView.as_view(), name="agent-version-publish"),
    path("agents/<uuid:agent_id>/versions/<str:version>/rollback/", AgentVersionRollbackView.as_view(), name="agent-version-rollback"),
    path("agents/<uuid:agent_id>/resources/", AgentResourcesView.as_view(), name="agent-resources"),
]
