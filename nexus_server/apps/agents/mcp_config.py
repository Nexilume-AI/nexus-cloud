from __future__ import annotations

from typing import Any

from django.conf import settings

from .models import Agent
from .validators import agent_mcp_server_name


MCP_API_KEY_PLACEHOLDER = "${NEXUS_API_KEY}"


def public_agent_mcp_url(*, request, agent: Agent) -> str:
    """Return the canonical MCP URL advertised to clients and Edge nodes."""

    base = str(getattr(settings, "NEXUS_PUBLIC_BASE_URL", "") or "").strip()
    if not base and request is not None:
        base = request.build_absolute_uri("/")
    return f"{base.rstrip('/')}/api/v1/agents/{agent.id}/mcp/"


def build_agent_mcp_export(
    *,
    request,
    agent: Agent,
    consumer_tenant_id: str | None,
    consumer_project_id: str | None,
    template: bool = False,
) -> dict[str, Any]:
    """Build the one canonical Agent MCP configuration.

    Consumer scope is deliberately supplied by the caller.  Marketplace Agents
    belong to the publisher, while the MCP request and API key always belong to
    the consuming workspace.
    """

    server_name = agent_mcp_server_name(agent_id=agent.id, name=agent.name)
    headers = {"Authorization": f"Bearer {MCP_API_KEY_PLACEHOLDER}"}
    scope_kind = "template" if template else ("project" if consumer_project_id else "workspace")
    if not template:
        if not consumer_tenant_id:
            raise ValueError("consumer_tenant_id is required for an executable MCP export")
        headers["X-Nexus-Tenant"] = str(consumer_tenant_id)
        if consumer_project_id:
            headers["X-Nexus-Project"] = str(consumer_project_id)

    server: dict[str, Any] = {
        "type": "streamable-http",
        "agent_id": str(agent.id),
        "name": server_name,
        "version": agent.current_version,
        "url": public_agent_mcp_url(request=request, agent=agent),
        "headers": headers,
    }
    if consumer_tenant_id:
        # Retained for existing clients.  It now consistently means consumer
        # workspace rather than the Agent publisher workspace.
        server["tenant_id"] = str(consumer_tenant_id)

    return {
        "agent_id": str(agent.id),
        "server_name": server_name,
        "version": agent.current_version,
        "transport": "streamable-http",
        "scope": {
            "kind": scope_kind,
            "tenant_id": str(consumer_tenant_id) if consumer_tenant_id else None,
            "project_id": str(consumer_project_id) if consumer_project_id else None,
        },
        "credential": {
            "mode": "bearer_env",
            "environment_variable": "NEXUS_API_KEY",
            "placeholder": MCP_API_KEY_PLACEHOLDER,
            "one_time_inline_supported": not template,
        },
        "template": template,
        "mcpServers": {server_name: server},
    }
