"""Verified source declarations; never import uploaded code on a Cloud worker."""
from rest_framework import exceptions

from .models import Agent
from .tool_catalog import TOOL_POLICY_FIELDS, _normalize_tools
from .workspace_grants import normalize_workspace_capabilities
from .mobile_access import normalize_mobile_capabilities


def normalize_agent_contract(value):
    if not isinstance(value, dict) or set(value) != {"computer", "mobile"}:
        raise ValueError("Invalid Agent contract")
    result = {}
    for resource, field, normalize in (
        ("computer", "workspace_capabilities", normalize_workspace_capabilities),
        ("mobile", "mobile_capabilities", normalize_mobile_capabilities),
    ):
        declaration = value[resource]
        if (not isinstance(declaration, dict) or set(declaration) != {"requirement", field}
            or declaration["requirement"] not in {"disabled", "optional", "required"}
            or not isinstance(declaration[field], list) or any(not isinstance(s, str) for s in declaration[field])):
            raise ValueError("Invalid Agent contract")
        scopes = normalize(declaration[field])
        if declaration["requirement"] == "disabled" and scopes:
            raise ValueError("Disabled resource declares scopes")
        if resource == "mobile" and declaration["requirement"] == "required" and not scopes:
            raise ValueError("Required Mobile has no scopes")
        result[resource] = {"requirement": declaration["requirement"], field: scopes}
    return result


def verified_python_catalog(raw_tools, *, native=False):
    from .python_builder import BuildFailure
    fields = (*TOOL_POLICY_FIELDS, "mobile_scopes", "input_modalities", "execution_profiles",
              "slash_command", "slash_description", "recovery_protocol")
    policies, contract = {}, None
    for tool in raw_tools:
        if not isinstance(tool, dict):
            raise BuildFailure("TOOL_CATALOG_INVALID", "The MCP tool catalog contains an invalid entry.")
        meta = tool.get("_meta", tool.get("meta", {}))
        nexus = meta.get("nexus", {}) if isinstance(meta, dict) else {}
        if not isinstance(nexus, dict):
            nexus = {}
        policies[str(tool.get("name", ""))] = {field: nexus[field] for field in fields if field in nexus}
        if native:
            try:
                declared = normalize_agent_contract(nexus.get("agent_contract"))
                if contract is not None and contract != declared:
                    raise ValueError("Conflicting declarations")
                if not set(nexus.get("mobile_scopes", [])).issubset(declared["mobile"]["mobile_capabilities"]):
                    raise ValueError("Undeclared Mobile scope")
                contract = declared
            except (ValueError, TypeError, exceptions.ValidationError):
                raise BuildFailure("AGENT_CONTRACT_INVALID", "The verified Agent resource declaration is invalid or inconsistent.") from None
    tools = _normalize_tools(raw_tools, version_policy=policies, edge_manifest=False)
    return tools, policies, contract


def activate_python_contract(*, agent, version):
    """Apply only on successful deployment, never while building a candidate.

    This is an Agent declaration, not a caller grant. Existing per-Run Caller
    ownership and intersection checks remain authoritative.
    """
    metadata = version.artifact_metadata if version else {}
    if not metadata or metadata.get("source_type") != "python_upload" or not metadata.get("agent_contract"):
        return
    contract = normalize_agent_contract(metadata["agent_contract"])
    changes = {
        "computer_requirement": contract["computer"]["requirement"],
        "workspace_capabilities": contract["computer"]["workspace_capabilities"],
        "mobile_requirement": contract["mobile"]["requirement"],
        "mobile_capabilities": contract["mobile"]["mobile_capabilities"],
    }
    Agent.objects.filter(pk=agent.pk).update(**changes)
    for key, value in changes.items():
        setattr(agent, key, value)
