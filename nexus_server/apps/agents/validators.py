from __future__ import annotations

import re

from rest_framework import serializers

AGENT_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
AGENT_NAME_HELP_TEXT = "Use English letters, numbers, underscores, or hyphens only. The first character must be a letter."


def validate_agent_name(value: str) -> str:
    name = str(value or "").strip()
    if not AGENT_NAME_PATTERN.fullmatch(name):
        raise serializers.ValidationError(AGENT_NAME_HELP_TEXT)
    return name


def agent_mcp_server_name(*, agent_id, name: str) -> str:
    text = str(name or "").strip()
    if AGENT_NAME_PATTERN.fullmatch(text):
        return text
    sanitized = re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-_")
    if sanitized and sanitized[0].isalpha():
        return sanitized[:64]
    return f"agent-{str(agent_id).replace('-', '')[:8]}"
