"""Shared Codex configuration codec; no credential issuance or IAM imports."""
from __future__ import annotations
import hashlib
import tomllib
from typing import Any
from apps.routers.models import Router
from .connection_core import WorkspaceError


def tool_config_revision(content: str) -> str:
    return hashlib.sha256(str(content or "").encode("utf-8")).hexdigest()


def _redacted_config(value: Any, *, key: str = "") -> Any:
    normalized_key = key.lower().replace("-", "_")
    if any(marker in normalized_key for marker in ("authorization", "token", "api_key", "password", "secret", "credential", "cookie")):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): _redacted_config(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_redacted_config(item) for item in value]
    return value


def _bearer_token(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower().startswith("bearer "):
        return text[7:].strip()
    return text


def _provider_token(parsed: dict[str, Any]) -> str:
    provider_name = str(parsed.get("model_provider") or "")
    providers = parsed.get("model_providers") if isinstance(parsed.get("model_providers"), dict) else {}
    provider = providers.get(provider_name) if isinstance(providers.get(provider_name), dict) else {}
    return str(provider.get("experimental_bearer_token") or "")


def _mcp_token(server: dict[str, Any]) -> str:
    # Codex uses `http_headers` for Streamable HTTP MCP servers. Older Nexus
    # builds wrote `headers`, which Codex silently ignored and consequently
    # reported AuthRequired even though Tool setup displayed Connected.
    headers = server.get("http_headers") if isinstance(server.get("http_headers"), dict) else {}
    return _bearer_token(headers.get("Authorization") or headers.get("authorization"))


def _configure_router_api_with_token(
    *, request, config: dict[str, Any], router: Router, models: list[str], token: str
) -> dict[str, Any]:
    updated = copy_config_dict(config)
    updated["model"] = models[0]
    updated["model_provider"] = "nexus"
    providers = dict(updated.get("model_providers") or {})
    providers["nexus"] = {
        "name": "Nexus Router",
        "base_url": request.build_absolute_uri("/api/v1/openai/v1").rstrip("/"),
        "wire_api": "responses",
        "experimental_bearer_token": token,
    }
    updated["model_providers"] = providers
    return updated


def _configure_agent_server(*, config: dict[str, Any], exported: dict[str, Any], token: str) -> tuple[dict[str, Any], str]:
    updated = copy_config_dict(config)
    servers = dict(updated.get("mcp_servers") or {})
    server_name = str(exported["server_name"])
    exported_server = (exported.get("mcpServers") or {}).get(server_name) or {}
    exported_headers = exported_server.get("http_headers")
    if not isinstance(exported_headers, dict):
        exported_headers = exported_server.get("headers") if isinstance(exported_server.get("headers"), dict) else {}
    servers[server_name] = {
        "url": str(exported_server.get("url") or ""),
        "http_headers": {
            **exported_headers,
            "Authorization": f"Bearer {token}",
        },
    }
    updated["mcp_servers"] = servers
    return updated, server_name


def _rewrite_managed_agent_tokens(*, config: dict[str, Any], managed: dict[str, Any], token: str) -> dict[str, Any]:
    updated = copy_config_dict(config)
    servers = dict(updated.get("mcp_servers") or {})
    for name in managed:
        server = servers.get(name)
        if not isinstance(server, dict):
            continue
        headers = dict(server.get("http_headers") or server.get("headers") or {})
        headers["Authorization"] = f"Bearer {token}"
        server = {key: value for key, value in server.items() if key != "headers"}
        server["http_headers"] = headers
        servers[name] = server
    updated["mcp_servers"] = servers
    return updated


def parse_codex_toml(content: str) -> dict[str, Any]:
    try:
        value = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise WorkspaceError(f"Remote Codex config is not valid TOML: {exc}") from exc
    return dict(value)


def codex_config_summary(*, path: str, exists: bool, content: str, parsed: dict[str, Any]) -> dict[str, Any]:
    provider_name = str(parsed.get("model_provider") or "")
    provider = (parsed.get("model_providers") or {}).get(provider_name, {}) if provider_name else {}
    servers = parsed.get("mcp_servers") or {}
    redacted_content = dump_toml(_redacted_config(parsed)) if parsed else ""
    return {
        "tool": "codex",
        "profile": "nexus",
        "path": path,
        "exists": exists,
        "launch_command": "codex --profile nexus",
        "model": str(parsed.get("model") or ""),
        "model_provider": provider_name,
        "api_configured": bool(provider.get("base_url")),
        "base_url": str(provider.get("base_url") or ""),
        "wire_api": str(provider.get("wire_api") or ""),
        "has_bearer_token": bool(provider.get("experimental_bearer_token")),
        "mcp_servers": [{"name": name, "url": str(server.get("url") or ""), "type": str(server.get("type") or "")} for name, server in servers.items() if isinstance(server, dict)],
        # ``content`` is retained as a compatibility alias, but is deliberately
        # redacted. New clients use ``redacted_content``.
        "content": redacted_content,
        "redacted_content": redacted_content,
    }


def copy_config_dict(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, dict):
            result[key] = copy_config_dict(item)
        elif isinstance(item, list):
            result[key] = [copy_config_dict(entry) if isinstance(entry, dict) else entry for entry in item]
        else:
            result[key] = item
    return result


def dump_toml(config: dict[str, Any]) -> str:
    lines: list[str] = []
    write_toml_table(lines=lines, table=config, prefix=[])
    return "\n".join(lines).strip() + "\n"


def write_toml_table(*, lines: list[str], table: dict[str, Any], prefix: list[str]) -> None:
    scalar_items = [(key, value) for key, value in table.items() if not isinstance(value, dict)]
    table_items = [(key, value) for key, value in table.items() if isinstance(value, dict)]
    if prefix:
        if lines and lines[-1] != "":
            lines.append("")
        lines.append(f"[{'.'.join(toml_key(part) for part in prefix)}]")
    for key, value in scalar_items:
        lines.append(f"{toml_key(key)} = {toml_value(value)}")
    for key, value in table_items:
        write_toml_table(lines=lines, table=value, prefix=[*prefix, str(key)])


def toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def toml_key(value: Any) -> str:
    text = str(value or "nexus")
    if text.replace("_", "").replace("-", "").isalnum():
        return text
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
