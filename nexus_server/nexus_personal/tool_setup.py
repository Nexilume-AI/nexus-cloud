"""Personal Tool Setup inspection/review and fenced API/Agent configuration."""
import tomllib
from urllib.parse import urlsplit, urlunsplit
from rest_framework import exceptions
from apps.workspaces.connection_core import WorkspaceError, WorkspaceConfigConflict
from apps.workspaces.execution import get_terminal_session
from apps.workspaces.context_policy import terminal_context_valid
from apps.workspaces.runtime_runner import ComputerRuntimeWorkspaceRunner
from apps.workspaces.tool_codec import tool_config_revision, _provider_token, dump_toml
from apps.workspaces.tool_runtime import require_tool_config_fencing
from .tool_profiles import get_profile, router_credential_state

PENDING_CODE = "PERSONAL_TOOL_SETUP_APPLY_UNAVAILABLE"
PENDING_MESSAGE = "Use independent API or Agent setup. Historical rollback and full-profile replacement are unavailable."
MAX_CONFIG_BYTES = 256 * 1024


class PersonalToolSetupUnavailable(WorkspaceError):
    """Keep the recovery code stable through the shared HTTP error envelope."""
    default_code = PENDING_CODE
    default_detail = PENDING_MESSAGE


def _session(request, session_id, tool="codex"):
    session = get_terminal_session(request=request, session_id=str(session_id))
    if not terminal_context_valid(session):
        raise WorkspaceError("Open an active Computer terminal before configuring tools.")
    if tool != "codex":
        raise WorkspaceError("Only Codex configuration is supported; Claude Code is detection-only.")
    # Also reject mismatched managed-profile ownership, even for inspection.
    get_profile(request=request, session_id=session_id)
    return session


def _read(session):
    result = ComputerRuntimeWorkspaceRunner().read_codex_config(connection=session.connection)
    content = result.get("content")
    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_CONFIG_BYTES:
        raise WorkspaceError("Computer configuration exceeds the supported size.")
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    try:
        parsed = tomllib.loads(content)
        if not isinstance(parsed.get("model_providers", {}), dict) or not isinstance(parsed.get("mcp_servers", {}), dict):
            raise ValueError()
    except (tomllib.TOMLDecodeError, ValueError):
        raise WorkspaceError("Computer configuration is not valid Codex TOML; repair it on the Computer and reload.") from None
    return result, content, parsed


def _text(value):
    return value[:256] if isinstance(value, str) else ""


def _public_url(value):
    # Unknown external URLs may carry credentials in *any* path segment. Do
    # not echo them; display only origin, with userinfo/query/fragment removed.
    try:
        url = urlsplit(_text(value))
        if url.scheme not in {"http", "https"} or not url.hostname:
            return ""
        host = "[" + url.hostname + "]" if ":" in url.hostname else url.hostname
        port = ":" + str(url.port) if url.port is not None else ""
        return urlunsplit((url.scheme, host + port, "", "", ""))
    except ValueError:
        return ""


def _provider(parsed):
    name = _text(parsed.get("model_provider"))
    row = parsed.get("model_providers", {}).get(name, {})
    return name, row if isinstance(row, dict) else {}


def _router(request, router_id):
    from apps.routers.services import get_mutable_router
    from apps.gateway.services import router_model_catalog
    from .gateway_integration import authorize_router
    router = get_mutable_router(request=request, router_id=str(router_id))
    authorize_router(request=request, tenant=router.tenant, router=router)
    models = [row["id"] for row in router_model_catalog(tenant=router.tenant, router=router) if row["available"]]
    if not models:
        raise WorkspaceError("Router has no currently available models.")
    return router, models


def _api_state(request, session_id, parsed, profile):
    name, provider = _provider(parsed)
    state = router_credential_state(request=request, session_id=session_id, remote_token=_provider_token(parsed))
    models = []
    if state == "ready" and profile.router_credential.issued_for != "tool_setup":
        state = "needs_repair"
    if state == "ready":
        try:
            _, models = _router(request, profile.router_id)
        except exceptions.APIException:
            state = "needs_repair"
        else:
            expected_base = request.build_absolute_uri("/api/v1/openai/v1").rstrip("/")
            if (name != "nexus" or provider.get("base_url") != expected_base or
                    provider.get("wire_api") != "responses" or parsed.get("model") not in models or
                    set(models) != set(profile.router_credential.model_names)):
                state = "needs_repair"
    return state, models


def _write_availability(session, profile):
    from .models import PersonalToolConfigOperation
    if profile is not None:
        pending = PersonalToolConfigOperation.objects.filter(profile=profile, active=True).first()
        if pending:
            return {"available": False, "code": "TOOL_CONFIG_OPERATION_PENDING",
                "message": "Recover the unconfirmed change before applying another configuration.",
                "operation_id": str(pending.pk), "state": pending.state}
    try:
        require_tool_config_fencing(session.connection)
    except exceptions.APIException:
        return {"available": False, "code": "COMPUTER_CAPABILITY_UNAVAILABLE",
            "message": "Connect an up-to-date Computer Runtime with revision-checked Tool Setup support."}
    return {"available": True, "code": "", "message": ""}


def _recovery_availability(session, profile):
    from .models import PersonalToolConfigOperation
    pending = PersonalToolConfigOperation.objects.filter(profile=profile, active=True).first() if profile else None
    result = {"rollback_available": False, "available": False, "actions": []}
    if pending is None:
        return result
    result.update(operation_id=str(pending.pk), state=pending.state)
    try:
        device = require_tool_config_fencing(session.connection)
    except exceptions.APIException:
        return {**result, "code":"COMPUTER_CAPABILITY_UNAVAILABLE",
            "message":"Reconnect an updated Computer Runtime before recovering this change."}
    if pending.command is None or pending.command.operation != 'tool_setup.write_codex_config_fenced' or pending.command.device_id != device.pk:
        return {**result, "code":"TOOL_CONFIG_RECOVERY_UNSUPPORTED",
            "message":"This older write cannot be safely fenced; repair its original Runtime first."}
    return {**result, "available":True, "actions":["recover", "restore_previous", "keep_local"],
        "code":"TOOL_CONFIG_RECOVERY_REQUIRED", "message":"Verify the local configuration before deciding whether to keep or restore it."}


def get_workspace_tool_config(*, request, session_id, tool="codex"):
    session = _session(request, session_id, tool)
    result, content, parsed = _read(session)
    profile = get_profile(request=request, session_id=session_id)
    name, provider = _provider(parsed)
    state, models = _api_state(request, session_id, parsed, profile)
    servers = [{"name": _text(key), "url": _public_url(row.get("url")), "type": "http" if row.get('url') else "stdio", "managed": False}
               for key, row in parsed.get("mcp_servers", {}).items() if isinstance(row, dict)]
    from .agent_tool_setup import summary as agent_summary
    agents=agent_summary(request,profile,parsed)
    managed_names={row['server_name'] for row in agents['managed']}
    for server in servers:
        server['managed']=server['name'] in managed_names
    agents['external']=[{'server_name':row['name'],'url':row['url'],'type':row['type']} for row in servers if not row['managed']]
    # An allowlist, not merely keyword redaction: arbitrary env/headers/args and
    # unknown fields from hand-written external MCP must never reach the UI.
    public = {"model": _text(parsed.get("model")), "model_provider": name,
        "model_providers": {name: {"base_url": _public_url(provider.get("base_url")),
            "wire_api": _text(provider.get("wire_api")), "experimental_bearer_token": "[REDACTED]"}} if name else {},
        "mcp_servers": {row["name"]: {"url": row["url"]} for row in servers}}
    redacted = dump_toml(public)
    return {"tool": "codex", "profile": "nexus", "path": "~/.codex/nexus.config.toml",
        "exists": bool(result.get("exists")), "revision": tool_config_revision(content),
        "launch_command": "codex --profile nexus", "model": public["model"], "model_provider": name,
        "base_url": _public_url(provider.get("base_url")), "wire_api": _text(provider.get("wire_api")),
        "has_bearer_token": bool(_provider_token(parsed)), "api_configured":state=='ready', "mcp_servers": servers,
        "redacted_content": redacted,
        "overview": {"target_name": session.connection.name, "session_status": session.status,
            "api_status": state, "connected_agent_count": len(agents['managed']), "external_mcp_count": len(agents['external']),
            "last_applied_at": profile.last_applied_at if profile else None},
        "api": {"status": state, "credential_managed": bool(profile and profile.router_credential_id),
            "router_id": str(profile.router_id) if profile and profile.router_id else None,
            "router_name": profile.router.name if profile and profile.router_id else "",
            "router_status": profile.router.status if profile and profile.router_id else "",
            "base_url": _public_url(provider.get('base_url')), "runtime_id":"", "runtime_name":"", "runtime_status":"",
            "models": models, "model": public["model"], "message": "Reload and review Router configuration." if state == "needs_repair" else ""},
        "agents": agents,
        "recovery": _recovery_availability(session, profile),
        "technical": {"redacted_content": redacted, "path":"~/.codex/nexus.config.toml", "launch_command":"codex --profile nexus"},
        "write_availability": _write_availability(session, profile)}


def get_workspace_tool_config_options(*, request, session_id, tool="codex"):
    session = _session(request, session_id, tool)
    from apps.routers.services import list_routers
    query = list_routers(request=request)
    search = _text(request.query_params.get("q", "")).strip()
    if search:
        query = query.filter(name__icontains=search)
    rows = list(query[:101])
    routers = []
    for row in rows[:100]:
        try:
            _, models = _router(request, row.pk)
        except exceptions.APIException:
            routers.append({"id": str(row.pk), "name": row.name, "available": False, "models": [],
                "code": "ROUTER_UNAVAILABLE", "message": "Deploy this Router and restore its model sources."})
        else:
            routers.append({"id": str(row.pk), "name": row.name, "available": True, "models": models,
                "code": "", "message": ""})
    from apps.agents.services import list_agents
    for item,router in zip(routers,rows[:100]):
        item.update(status=router.status,router_type=router.router_type,strategy=router.strategy,
            model=item['models'][0] if item['models'] else '')
    from .agent_tool_setup import agent_for_add
    candidates=list_agents(request=request)
    if search:
        candidates=candidates.filter(name__icontains=search)
    agent_rows=list(candidates[:101])
    agents=[]
    for agent in agent_rows[:100]:
        try:
            agent_for_add(request,str(agent.pk))
        except exceptions.APIException:
            agents.append({'id':str(agent.pk),'name':agent.name,'available':False,
                'code':'AGENT_RUNTIME_UNAVAILABLE','message':'Enable the Agent and restore its runtime.'})
        else:
            agents.append({'id':str(agent.pk),'name':agent.name,'available':True,'code':'','message':''})
        agents[-1].update(status=agent.status,version=agent.current_version or '',visibility='Personal')
    return {"tool": "codex", "routers": routers, "has_more": len(rows) > 100,
        "agents": agents, "runtimes": [], "agents_has_more":len(agent_rows)>100, "agents_available": True, "agents_code": "",
        "write_availability": _write_availability(session, get_profile(request=request, session_id=session_id))}


def preview_workspace_tool_config(*, request, session_id, data):
    session = _session(request, session_id, data.get("tool", "codex"))
    if data.get('section')=='agents':
        from .agent_tool_setup import preview
        require_tool_config_fencing(session.connection)
        _,content,parsed=_read(session)
        revision=tool_config_revision(content)
        if data.get('expected_revision') and data['expected_revision']!=revision:
            raise WorkspaceConfigConflict()
        profile=get_profile(request=request,session_id=session_id)
        return preview(request,profile,parsed,data,revision,_write_availability(session,profile))
    if data.get("section") != "api" or data.get("action") not in {"set_router", "rotate_api_credential"}:
        raise PersonalToolSetupUnavailable()
    require_tool_config_fencing(session.connection)
    _, content, parsed = _read(session)
    revision = tool_config_revision(content)
    if data.get("expected_revision") and data["expected_revision"] != revision:
        raise WorkspaceConfigConflict()
    profile = get_profile(request=request, session_id=session_id)
    router_id = data.get("router_id") or (profile.router_id if profile else None)
    if not router_id:
        raise WorkspaceError("Choose a Router before reviewing API setup.")
    router, models = _router(request, router_id)
    state, _ = _api_state(request, session_id, parsed, profile)
    reuse = state == "ready" and profile.router_id == router.pk
    action = "rotate" if data["action"] == "rotate_api_credential" else "reuse" if reuse else "repair" if profile else "create"
    availability = _write_availability(session, profile)
    return {"tool": "codex", "section": "api", "action": data["action"], "revision": revision,
        "changes": [{"kind": "preserved" if reuse else "modified", "label": "Router API",
            "before": _text(parsed.get("model")), "after": models[0]},
            {"kind": "preserved", "label": "Existing MCP servers", "before": str(len(parsed.get("mcp_servers", {}))),
             "after": str(len(parsed.get("mcp_servers", {})))}],
        "router_id": str(router.pk), "models": models, "credential_action": action,
        "warnings": [] if availability["available"] else [availability], "destructive": False, "can_apply": availability["available"]}


def apply_workspace_tool_config_change(*, request, session_id, data):
    from .tool_apply import apply
    return apply(request=request, session_id=session_id, data=data)


def apply_workspace_tool_config(*, request, session_id, data):
    _session(request, session_id, data.get("tool", "codex"))
    raise WorkspaceError("Use the Router API review/apply workflow; legacy full-profile writes are not available.")


def rollback_workspace_tool_config(*, request, session_id, tool="codex"):
    _session(request, session_id, tool)
    raise PersonalToolSetupUnavailable()
