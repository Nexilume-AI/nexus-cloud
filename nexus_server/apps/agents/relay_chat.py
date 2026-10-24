"""Explicit rollout for Relay installations verified with streamed Chat tasks."""
from django.conf import settings


def relay_chat_enabled(runtime) -> bool:
    if runtime.runtime_kind != "openwrt_relay":
        return False
    allowed = getattr(settings, "NEXUS_AGENT_RELAY_CHAT_AGENTS", ())
    return str(runtime.agent_id) in allowed


def relay_mobile_enabled(runtime, *, policy) -> bool:
    """Resolve Mobile support from the Agent and authenticated node contracts."""
    from .device_contracts import current_mobile_declaration
    if runtime.runtime_kind != "openwrt_relay":
        return False
    requirement, scopes = current_mobile_declaration(runtime.agent)
    if requirement == "disabled" or not scopes:
        return False
    registration = runtime.edge_registration
    if registration is None or registration.status != "active" or registration.transport != "relay":
        return False
    capabilities = (registration.node.capabilities if registration is not None and registration.node_id else {}) or {}
    required = {"relay", "runtime_context_v1", "invoke_interactions_v1", "mcp_stream_v1", "caller_mobile_v1"}
    if policy.get("task"):
        required.add("mcp_tasks_v1")
    return all(capabilities.get(name) is True for name in required)


def collect_relay_task_result(chunks, request_id):
    """Drain heartbeats without buffering them; retain exactly one final packet."""
    import json
    result = None
    for chunk in chunks:
        if chunk.startswith(b":"):
            continue
        data = [line[5:].lstrip() for line in chunk.splitlines() if line.startswith(b"data:")]
        if not data:
            continue
        packet = json.loads(b"\n".join(data))
        if packet.get("id") == request_id and ("result" in packet or "error" in packet):
            if result is not None:
                raise ValueError("Duplicate Relay task result")
            result = json.dumps(packet, separators=(",", ":")).encode()
            if len(result) > 1048576:
                raise ValueError("Relay task result exceeds bound")
    if result is None:
        raise ValueError("Relay task ended without result")
    return result
