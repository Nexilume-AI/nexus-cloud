from django.urls import path
from .conversation_views import (
    AgentInvocationView, AgentInteractorView, AgentPrivateRunListCreateView, PrivateAgentRunCancelView, PrivateAgentRunRecoveryView, PrivateAgentRunResumeView,
)
from .follow_up_views import PrivateRunFollowUpsView, PrivateRunFollowUpCancelView, InternalRunInboxView

urlpatterns = [
    path("agent-runs/<uuid:run_id>/cancel/", PrivateAgentRunCancelView.as_view(), name="private-agent-run-cancel"),
    path("agent-runs/<uuid:run_id>/recovery/", PrivateAgentRunRecoveryView.as_view(), name="private-agent-run-recovery"),
    path("agent-runs/<uuid:run_id>/resume/", PrivateAgentRunResumeView.as_view(), name="private-agent-run-resume"),
    path("agents/<uuid:agent_id>/invocations/", AgentInvocationView.as_view(), name="agent-invocation"),
    path("agents/<uuid:agent_id>/interactor/", AgentInteractorView.as_view(), name="agent-interactor"),
    path("agents/<uuid:agent_id>/private-runs/", AgentPrivateRunListCreateView.as_view(), name="agent-private-runs"),
    path("agent-runs/<uuid:run_id>/follow-ups/", PrivateRunFollowUpsView.as_view()),
    path("agent-runs/<uuid:run_id>/follow-ups/<uuid:message_id>/", PrivateRunFollowUpCancelView.as_view()),
    path("internal/agent-runs/<uuid:run_id>/inbox/", InternalRunInboxView.as_view()),
]
