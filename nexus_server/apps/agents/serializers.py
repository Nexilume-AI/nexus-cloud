"""Shared Agent serializers with explicitly selected distribution presentation."""
from .serializer_core import (
    active_related_items,
    current_agent_image,
    configured_runtime,
    AgentCreateSerializer,
    AgentUpdateSerializer,
    AgentCloneSerializer,
    AgentVersionPublishSerializer,
    AgentComputerBindingSerializer,
    AgentComputerBindingCreateSerializer,
    AgentMobileBindingSerializer,
    AgentMobileBindingCreateSerializer,
    AgentMobileBindingUpdateSerializer,
    AgentMobileGrantSerializer,
    AgentMobileGrantSetSerializer,
    AgentWorkspaceGrantSerializer,
    AgentWorkspaceGrantSetSerializer,
    AgentVersionSerializer,
    AgentDeploymentSerializer,
    AgentDeploySerializer,
    AgentLogSerializer,
    AgentOutputArtifactSerializer,
    AgentMemoryItemSerializer,
    AgentMemoryItemCreateSerializer,
    AgentResourceConfigSerializer,
    AgentResourceSetSerializer,
    AgentEndpointSerializer,
    latest_runtime_deployment,
)
from .presentation import configured_serializer, serializer_export

AgentSerializer = configured_serializer("AgentSerializer")
AgentDisplayRunSerializer = configured_serializer("AgentDisplayRunSerializer")


def __getattr__(name):
    if name in {
        "publication_checks", "listing_profile_checks", "AgentPublicationSerializer",
        "AgentVisibilitySerializer", "AgentPricingSerializer", "AgentToolPricingLimitInputSerializer",
        "AgentPricingSetSerializer", "MarketplaceAgentSerializer", "marketplace_agent_metrics",
    }:
        return serializer_export(name)
    raise AttributeError(name)
