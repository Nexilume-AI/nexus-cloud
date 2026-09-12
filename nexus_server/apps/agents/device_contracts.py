"""Shared device declaration schemas; no transport, permissions or billing imports."""
from __future__ import annotations
from typing import Iterable
from rest_framework import exceptions
from .models import Agent


WORKSPACE_CAPABILITIES = (
    "connection.list",
    "connection.create",
    "connection.update",
    "connection.delete",
    "connection.test",
    "connection.bind",
    "files.list",
    "files.read",
    "files.write",
    "command.execute",
    "browser.control",
)


WORKSPACE_CAPABILITY_SET = frozenset(WORKSPACE_CAPABILITIES)


WORKSPACE_SETUP_TOOLS = frozenset(
    {
        "nexus_workspace_connections_list",
        "nexus_workspace_connection_create",
        "nexus_workspace_connection_update",
        "nexus_workspace_connection_delete",
        "nexus_workspace_connection_test",
        "nexus_workspace_computer_bind",
    }
)


def normalize_workspace_capabilities(values: Iterable[str] | None) -> list[str]:
    result: list[str] = []
    for raw in values or ():
        value = str(raw or "").strip()
        if value not in WORKSPACE_CAPABILITY_SET:
            raise exceptions.ValidationError({"workspace_capabilities": f"Unsupported Workspace capability: {value}"})
        if value not in result:
            result.append(value)
    return [value for value in WORKSPACE_CAPABILITIES if value in result]


MOBILE_CAPABILITIES = (
    "mobile.observe",
    "mobile.screen.capture",
    "mobile.tap",
    "mobile.type_text",
    "mobile.swipe",
    "mobile.press_back",
    "mobile.open_app",
    "mobile.wait_for_state",
)


MOBILE_CAPABILITY_SET = frozenset(MOBILE_CAPABILITIES)


def normalize_mobile_capabilities(values: Iterable[str] | None) -> list[str]:
    requested: list[str] = []
    for raw in values or ():
        value = str(raw or "").strip()
        if value not in MOBILE_CAPABILITY_SET:
            raise exceptions.ValidationError({"mobile_capabilities": f"Unsupported Mobile capability: {value}"})
        if value not in requested:
            requested.append(value)
    return [value for value in MOBILE_CAPABILITIES if value in requested]


def current_mobile_declaration(agent: Agent) -> tuple[str, list[str]]:
    """Return the Agent control-plane Mobile policy.

    AgentVersion and deployment fields are historical snapshots only. Every
    caller-facing read and every new Run must resolve through this function.
    """

    requirement = agent.mobile_requirement or Agent.MOBILE_DISABLED
    capabilities = normalize_mobile_capabilities(agent.mobile_capabilities)
    if requirement == Agent.MOBILE_DISABLED:
        capabilities = []
    return requirement, capabilities


def sdk_mobile_declaration(registration) -> tuple[str, list[str]]:
    """Normalize the latest OpenWrt SDK declaration saved on a registration."""

    requirement = registration.mobile_requirement or Agent.MOBILE_DISABLED
    capabilities = normalize_mobile_capabilities(registration.mobile_capabilities)
    if not registration.mobile_requirement or requirement == Agent.MOBILE_DISABLED:
        capabilities = []
    return requirement, capabilities

