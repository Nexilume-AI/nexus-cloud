from django.urls import path
from .mcp_views import AgentMCPProxyView, AgentLegacySSEProxyView, AgentLegacySSEMessagesProxyView

urlpatterns = [
    path('agents/<uuid:agent_id>/mcp/', AgentMCPProxyView.as_view(), name='agent-mcp-proxy'),
    path('agents/<uuid:agent_id>/mcp/sse/', AgentLegacySSEProxyView.as_view(), name='agent-mcp-legacy-sse'),
    path('agents/<uuid:agent_id>/mcp/messages/', AgentLegacySSEMessagesProxyView.as_view(), name='agent-mcp-legacy-messages'),
    path('agents/<uuid:agent_id>/sse/', AgentLegacySSEProxyView.as_view(), name='agent-legacy-sse'),
    path('agents/<uuid:agent_id>/messages/', AgentLegacySSEMessagesProxyView.as_view(), name='agent-legacy-messages'),
]
