from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from apps.common.models import SoftDeleteModel

from .models import Agent, AgentRuntimeDeployment, AgentVersion


TOOL_POLICY_FIELDS = ("task", "continuable", "demo", "chat", "interactive")
MOBILE_SCOPE_FIELDS = ("mobile_scopes",)
DEFAULT_INPUT_SCHEMA = {"type": "object", "additionalProperties": True}
TOOL_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,126}")
SLASH_COMMAND_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
RESERVED_PRIVATE_DISPLAY_SLASH_COMMANDS = frozenset({"new", "cancel", "model", "help"})
PROFILE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}


def tool_contract_digest(descriptor: dict[str, Any] | None) -> str:
    """Hash only execution semantics, excluding health/availability UI state."""

    item = descriptor if isinstance(descriptor, dict) else {}
    policy = item.get("policy") if isinstance(item.get("policy"), dict) else item
    payload = {
        "name": str(item.get("name") or ""),
        "input_schema": item.get("input_schema") if isinstance(item.get("input_schema"), dict) else {},
        "input_modalities": list(item.get("input_modalities") or []),
        "mobile_scopes": list(item.get("mobile_scopes") or []),
        "execution_profiles": list(item.get("execution_profiles") or []),
        "recovery_protocol": int(item.get("recovery_protocol") or 0),
        **{field: bool(policy.get(field)) for field in TOOL_POLICY_FIELDS},
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def display_mode_for_policy(policy: dict[str, Any]) -> str:
    if policy.get("chat"):
        return "chat"
    if policy.get("task"):
        return "task"
    if policy.get("interactive"):
        return "interactive"
    return "tool"


def interaction_tools(
    *, agent: Agent, runtime: AgentRuntimeDeployment | None
) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    catalog = effective_mcp_tools(agent=agent, runtime=runtime)
    for item in catalog:
        policy = {field: bool(item.get(field)) for field in TOOL_POLICY_FIELDS}
        properties = item["input_schema"].get("properties", {})
        modalities = list(item.get("input_modalities") or ["text"])
        if properties.get("attachments", {}).get("type") == "array":
            if "image" not in modalities: modalities.append("image")
        if properties.get("audio", {}).get("type") == "array":
            if "audio" not in modalities: modalities.append("audio")
        tools.append(
            {
                "name": item["name"],
                "title": item["title"],
                "description": item["description"],
                "input_schema": item["input_schema"],
                "input_modalities": modalities,
                "mobile_scopes": list(item.get("mobile_scopes") or []),
                "accepts_files": properties.get("files", {}).get("type") == "array",
                "slash_command": item.get("slash_command", ""),
                "slash_description": item.get("slash_description", ""),
                "execution_profiles": item.get("execution_profiles", []),
                "recovery_protocol": int(item.get("recovery_protocol") or 0),
                "policy": policy,
                "display_mode": display_mode_for_policy(policy),
                "availability": tool_availability(
                    runtime=runtime,
                    policy=policy,
                ),
            }
        )
    return tools


def tool_availability(
    *,
    runtime: AgentRuntimeDeployment | None,
    policy: dict[str, Any],
) -> dict[str, Any]:
    if runtime is None or runtime.effective_status() != AgentRuntimeDeployment.STATUS_ACTIVE:
        return {
            "can_invoke": False,
            "code": "RUNTIME_UNAVAILABLE",
            "message": "The Agent runtime is not currently available.",
        }
    if (
        display_mode_for_policy(policy) != "tool"
        and runtime.runtime_kind == AgentRuntimeDeployment.RUNTIME_OPENWRT_RELAY
    ):
        return {
            "can_invoke": False,
            "code": "OPENWRT_IPV6_REQUIRED",
            "message": "This interaction mode currently requires Direct IPv6.",
        }
    return {"can_invoke": True, "code": "", "message": ""}


def effective_mcp_tools(
    *,
    agent: Agent,
    runtime: AgentRuntimeDeployment | None = None,
) -> list[dict[str, Any]]:
    """Return the current callable MCP catalog without persisting dynamic manifests."""

    version = _current_version(agent)
    version_policy = version.tool_runtime_policy or {} if version is not None else {}
    if not isinstance(version_policy, dict):
        version_policy = {}

    registration = getattr(runtime, "edge_registration", None) if runtime is not None else None
    if registration is not None:
        return _normalize_tools(
            registration.mcp_tools or [],
            version_policy=version_policy,
            edge_manifest=True,
        )

    # Python candidates carry the catalog verified for that immutable image.
    # Never let an undeployed candidate's declaration replace a live version.
    image = getattr(runtime, "image", None) if runtime is not None else getattr(agent, "current_image", None)
    image_version = getattr(image, "version", None) if image is not None else None
    if image_version and (image_version.artifact_metadata or {}).get("source_type") == "python_upload":
        return _normalize_tools(image_version.artifact_metadata.get("tools", []),
            version_policy=image_version.tool_runtime_policy or {}, edge_manifest=False)

    metadata = agent.repo_metadata or {}
    raw_tools = metadata.get("tools", []) if isinstance(metadata, dict) else []
    tools = _normalize_tools(
        raw_tools if isinstance(raw_tools, list) else [],
        version_policy=version_policy,
        edge_manifest=False,
    )
    known_names = {item["name"] for item in tools}
    for name, policy in version_policy.items():
        normalized_name = str(name or "").strip()
        if normalized_name in known_names or not TOOL_NAME_PATTERN.fullmatch(normalized_name):
            continue
        descriptor = _normalize_tool(
            {"name": normalized_name},
            version_policy=policy,
            edge_manifest=False,
        )
        if descriptor is not None:
            tools.append(descriptor)
            known_names.add(normalized_name)
    return tools


def runtime_tool_policy(
    *,
    agent: Agent,
    runtime: AgentRuntimeDeployment,
    tool_name: str,
) -> dict[str, Any]:
    name = str(tool_name or "").strip()
    descriptor = next(
        (item for item in effective_mcp_tools(agent=agent, runtime=runtime) if item["name"] == name),
        None,
    )
    result: dict[str, Any] = {
        field: bool(descriptor.get(field)) if descriptor is not None else False
        for field in TOOL_POLICY_FIELDS
    }
    result["mobile_scopes"] = list(descriptor.get("mobile_scopes") or []) if descriptor is not None else []
    result["slash_command"] = str(descriptor.get("slash_command") or "") if descriptor is not None else ""
    result["slash_description"] = str(descriptor.get("slash_description") or "") if descriptor is not None else ""
    result["execution_profiles"] = list(descriptor.get("execution_profiles") or []) if descriptor is not None else []
    result["recovery_protocol"] = int(descriptor.get("recovery_protocol") or 0) if descriptor is not None else 0
    return result


def public_demo_tools(
    *,
    agent: Agent,
    runtime: AgentRuntimeDeployment | None,
) -> list[dict[str, Any]]:
    return [
        {
            "name": item["name"],
            "title": item["title"],
            "description": item["description"],
            "input_schema": item["input_schema"],
        }
        for item in effective_mcp_tools(agent=agent, runtime=runtime)
        if item["demo"]
    ]


def _normalize_tools(
    raw_tools: list[Any],
    *,
    version_policy: dict[str, Any],
    edge_manifest: bool,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    names: set[str] = set()
    slash_commands: set[str] = set()
    for raw_tool in raw_tools:
        if isinstance(raw_tool, str):
            raw_tool = {"name": raw_tool}
        if not isinstance(raw_tool, dict):
            continue
        name = str(raw_tool.get("name") or raw_tool.get("id") or "").strip()
        if name in names or not TOOL_NAME_PATTERN.fullmatch(name):
            continue
        schema = raw_tool.get("input_schema", raw_tool.get("inputSchema", DEFAULT_INPUT_SCHEMA))
        if not isinstance(schema, dict):
            continue
        descriptor = _normalize_tool(
            raw_tool,
            version_policy=version_policy.get(name, {}),
            edge_manifest=edge_manifest,
        )
        if descriptor is None:
            continue
        command = str(descriptor.get("slash_command") or "")
        if command:
            properties = descriptor["input_schema"].get("properties", {})
            if (
                command in RESERVED_PRIVATE_DISPLAY_SLASH_COMMANDS
                or command in slash_commands
                or not descriptor.get("task")
                or not ({"content", "message"} & set(properties))
            ):
                descriptor["slash_command"] = ""
                descriptor["slash_description"] = ""
            else:
                slash_commands.add(command)
        normalized.append(descriptor)
        names.add(name)
    return normalized


def _normalize_tool(
    raw_tool: dict[str, Any],
    *,
    version_policy: Any,
    edge_manifest: bool,
) -> dict[str, Any] | None:
    name = str(raw_tool.get("name") or raw_tool.get("id") or "").strip()
    if not TOOL_NAME_PATTERN.fullmatch(name):
        return None
    schema = raw_tool.get("input_schema", raw_tool.get("inputSchema", DEFAULT_INPUT_SCHEMA))
    if not isinstance(schema, dict):
        return None
    policy = {
        field: bool(raw_tool.get(field))
        for field in TOOL_POLICY_FIELDS
        if field in raw_tool
    }
    version_values = dict(version_policy) if isinstance(version_policy, dict) else {}
    raw_mobile_scopes = raw_tool.get("mobile_scopes", version_values.get("mobile_scopes", []))
    mobile_scopes = [str(value) for value in raw_mobile_scopes] if isinstance(raw_mobile_scopes, list) else []
    if edge_manifest:
        policy = {
            field: (
                bool(raw_tool.get(field))
                if field in raw_tool
                else False if field == "demo" else bool(version_values.get(field))
            )
            for field in TOOL_POLICY_FIELDS
        }
    else:
        policy.update({field: bool(version_values.get(field)) for field in TOOL_POLICY_FIELDS if field in version_values})
    if policy.get("chat"):
        policy["task"] = True
        policy["interactive"] = True
    title = str(raw_tool.get("title") or name).strip() or name
    description = str(raw_tool.get("description") or raw_tool.get("intent") or name).strip() or name
    slash_command = str(raw_tool.get("slash_command") or version_values.get("slash_command") or "").strip().removeprefix("/")
    if slash_command and not SLASH_COMMAND_PATTERN.fullmatch(slash_command):
        slash_command = ""
    slash_description = str(raw_tool.get("slash_description") or version_values.get("slash_description") or "").strip()[:160]
    raw_profiles = raw_tool.get("execution_profiles", version_values.get("execution_profiles", []))
    raw_modalities = raw_tool.get("input_modalities", version_values.get("input_modalities", ["text"]))
    input_modalities = list(dict.fromkeys(
        str(value).strip().lower() for value in raw_modalities
        if str(value).strip().lower() in {"text", "image", "audio"}
    )) if isinstance(raw_modalities, list) else ["text"]
    if not input_modalities:
        input_modalities = ["text"]
    execution_profiles: list[dict[str, Any]] = []
    seen_profiles: set[str] = set()
    default_profile_seen = False
    if isinstance(raw_profiles, list):
        for raw_profile in raw_profiles[:8]:
            if not isinstance(raw_profile, dict):
                continue
            profile_id = str(raw_profile.get("id") or "").strip()
            label = str(raw_profile.get("label") or "").strip()[:80]
            model = str(raw_profile.get("model") or "").strip()[:128]
            efforts = list(dict.fromkeys(
                str(value).strip().lower() for value in raw_profile.get("reasoning_efforts", [])
                if str(value).strip().lower() in REASONING_EFFORTS
            )) if isinstance(raw_profile.get("reasoning_efforts", []), list) else []
            default_effort = str(raw_profile.get("default_reasoning_effort") or "").strip().lower()
            context_window = raw_profile.get("context_window")
            raw_is_default = raw_profile.get("is_default", False)
            if (
                profile_id in seen_profiles or not PROFILE_ID_PATTERN.fullmatch(profile_id)
                or not label or not model or (default_effort and default_effort not in efforts)
                or not isinstance(raw_is_default, bool)
                or (raw_is_default and default_profile_seen)
                or (context_window is not None and (isinstance(context_window, bool) or not isinstance(context_window, int) or context_window <= 0))
            ):
                continue
            if efforts and not default_effort:
                default_effort = efforts[0]
            execution_profiles.append({
                "id": profile_id, "label": label, "model": model,
                "is_default": raw_is_default,
                "reasoning_efforts": efforts,
                "default_reasoning_effort": default_effort,
                "context_window": context_window,
            })
            seen_profiles.add(profile_id)
            default_profile_seen = default_profile_seen or raw_is_default
    if execution_profiles and not default_profile_seen:
        execution_profiles[0]["is_default"] = True
    return {
        "name": name,
        "title": title,
        "description": description,
        "input_schema": schema,
        **{field: bool(policy.get(field)) for field in TOOL_POLICY_FIELDS},
        "mobile_scopes": mobile_scopes,
        "slash_command": slash_command,
        "slash_description": slash_description or (description if slash_command else ""),
        "execution_profiles": execution_profiles,
        "input_modalities": input_modalities,
        "recovery_protocol": 1 if int(raw_tool.get("recovery_protocol") or version_values.get("recovery_protocol") or 0) >= 1 else 0,
    }


def _current_version(agent: Agent) -> AgentVersion | None:
    prefetched = getattr(agent, "_prefetched_objects_cache", {}).get("versions")
    versions = list(prefetched) if prefetched is not None else None
    if versions is not None:
        candidates = [item for item in versions if item.status != SoftDeleteModel.STATUS_DELETED]
        if agent.current_version:
            return next((item for item in candidates if item.version == agent.current_version), None)
        return max([item for item in candidates if (item.artifact_metadata or {}).get("source_type") != "python_upload"], key=lambda item: item.created_at, default=None)
    queryset = agent.versions.exclude(status=SoftDeleteModel.STATUS_DELETED)
    if agent.current_version:
        return queryset.filter(version=agent.current_version).first()
    return queryset.exclude(artifact_metadata__has_key="source_type", artifact_metadata__source_type="python_upload").order_by("-created_at").first()
