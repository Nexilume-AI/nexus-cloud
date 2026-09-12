from __future__ import annotations

import uuid
import hashlib
import hmac
import json
import mimetypes
import posixpath
import re
import secrets
from decimal import Decimal
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from django.db import transaction
from django.core.cache import cache
from django.db.models import Q, Sum
from django.conf import settings
from django.utils import timezone
from rest_framework import exceptions, status

from apps.audit.services import log_audit
from apps.common.models import SoftDeleteModel
from apps.common.project_scope import scope_queryset_to_current_project
from apps.common.subjects import hash_token, request_subject
from apps.common.authorization import has_nexus_permission
from apps.common.resource_catalog import (
    discoverable_resource_queryset,
    resolve_ownership_project,
)
from apps.tenancy.models import Project, Team, Tenant
from apps.common.request_context import get_tenant_from_request

from .models import (
    Agent,
    AgentComputerBinding,
    AgentDeployment,
    AgentDisplayEvent,
    AgentDisplayAsset,
    AgentDisplayRun,
    AgentExecutionTask,
    AgentLog,
    AgentMemoryItem,
    AgentOutputArtifact,
    AgentRunInteraction,
    AgentRunMessage,
    AgentResourceConfig,
    AgentRuntimeDeployment,
    AgentVersion,
    EdgeAgentRegistration,
)
from .tool_catalog import interaction_tools
from .mcp_config import build_agent_mcp_export
from .validators import validate_agent_name
from .policy import invoke_agent_policy

AGUI_CUSTOM_COMPUTER_LOG = "nexus.computer.log"
AGUI_CUSTOM_COMPUTER_FRAME = "nexus.computer.frame"
AGUI_CUSTOM_FILE_CREATED = "nexus.file.created"
AGUI_CUSTOM_FILE_UPDATED = "nexus.file.updated"
AGUI_CUSTOM_MEMORY_ITEM = "nexus.memory.item"
AGUI_CUSTOM_MEMORY_ITEM_UPDATED = "nexus.memory.item.updated"
AGUI_CUSTOM_MEMORY_ITEM_DELETED = "nexus.memory.item.deleted"
AGUI_CUSTOM_DELIVERABLE_READY = "nexus.deliverable.ready"
AGUI_CUSTOM_RUNTIME_STATUS = "nexus.runtime.status"
AGUI_SCREENSHOT_URL_MAX_LENGTH = 1_500_000
AGUI_DEFAULT_STRING_MAX_LENGTH = 4096
AGUI_LARGE_STRING_KEYS = {"screenshot_url", "screenshotUrl", "image_url", "imageUrl", "content_url", "contentUrl"}
from apps.common.redaction import (REDACTION_EMAIL_RE, REDACTION_BEARER_RE,
    REDACTION_SECRET_RE, REDACTION_PHONE_RE, REDACTION_SENSITIVE_KEYS)
TEXT_OUTPUT_CONTENT_TYPES = {"application/json", "application/jsonl", "text/csv", "text/markdown", "text/plain"}
AGUI_TYPE_ALIASES = {
    "RunStarted": AgentDisplayEvent.TYPE_RUN_STARTED,
    "RunFinished": AgentDisplayEvent.TYPE_RUN_COMPLETED,
    "RunError": AgentDisplayEvent.TYPE_RUN_FAILED,
    "ActivitySnapshot": AgentDisplayEvent.TYPE_PLAN_UPDATED,
    "ActivityDelta": "ACTIVITY_DELTA",
    "TextMessageStart": "TEXT_MESSAGE_START",
    "TextMessageContent": AgentDisplayEvent.TYPE_CHAT_MESSAGE,
    "TextMessageEnd": "TEXT_MESSAGE_END",
    "TextMessageChunk": AgentDisplayEvent.TYPE_CHAT_MESSAGE,
    "MessagesSnapshot": "MESSAGES_SNAPSHOT",
    "ToolCallStart": AgentDisplayEvent.TYPE_TOOL_STARTED,
    "ToolCallArgs": "TOOL_CALL_ARGS",
    "ToolCallResult": "TOOL_CALL_RESULT",
    "ToolCallEnd": AgentDisplayEvent.TYPE_TOOL_COMPLETED,
    "StateSnapshot": "STATE_SNAPSHOT",
    "StateDelta": "STATE_DELTA",
    "Custom": "CUSTOM",
}


class AgentNotFound(exceptions.APIException):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "Agent not found."
    default_code = "NOT_FOUND"


class AgentEventConflict(exceptions.APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "AG-UI event ID was already used with different content."
    default_code = "AGUI_EVENT_CONFLICT"


def list_agents(*, request):
    return invoke_agent_policy("list_agents", request=request)


@transaction.atomic
def create_agent(*, request, name: str, ownership: dict | None = None) -> Agent:
    tenant = get_tenant_from_request(request)
    require_agent_admin(request=request, tenant=tenant)
    from apps.common.resource_limits import enforce_capability

    Tenant.objects.select_for_update(no_key=True).get(pk=tenant.pk)
    enforce_capability(tenant=tenant, code="agents.agents")
    name = validate_agent_name(name)
    project, _ownership_inferred = resolve_ownership_project(request=request, tenant=tenant, ownership=ownership)
    team = resolve_team_from_request(request=request, tenant=tenant)
    agent = Agent.objects.create(
        tenant=tenant,
        project=project,
        team=team,
        name=name,
        status=Agent.STATUS_DRAFT,
        visibility=Agent.VISIBILITY_PRIVATE,
        created_by=request.user,
    )
    log_audit(
        request=request,
        action="agents.create",
        actor=request.user,
        resource_type="agent",
        resource_id=agent.pk,
        metadata={"name": name},
    )
    return agent


def get_agent(*, request, agent_id: str) -> Agent:
    tenant = get_tenant_from_request(request)
    agent = discoverable_resource_queryset(
        visible_agents(user=request.user, tenant=tenant), request=request,
        tenant=tenant, resource_type="agent",
    ).filter(id=agent_id).first()
    if agent is None:
        raise AgentNotFound()
    return agent


def list_computer_bindings(*, request, agent_id: str):
    tenant = get_tenant_from_request(request)
    subject = request_subject(request)
    agent = _get_bindable_agent(request=request, agent_id=agent_id)
    return (
        AgentComputerBinding.objects.filter(
            tenant=tenant,
            agent=agent,
            caller_subject_hash=subject.subject_hash,
        )
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .select_related("connection")
        .order_by("-is_default", "-created_at")
    )


@transaction.atomic
def create_computer_binding(*, request, agent_id: str, connection_id: str, is_default: bool = True) -> AgentComputerBinding:
    from apps.workspaces.connection_core import get_workspace_connection, require_workspace_own
    from apps.workspaces.computer_runtime import (
        ComputerCapabilityUnavailable,
        ensure_runtime_connection,
    )
    from apps.workspaces.models import WorkspaceConnection

    tenant = get_tenant_from_request(request)
    subject = request_subject(request)
    agent = _get_bindable_agent(request=request, agent_id=agent_id)
    connection = get_workspace_connection(request=request, connection_id=connection_id)
    require_workspace_own(request=request, tenant=tenant, action="workspace.connection.use_own", connection=connection)
    if connection.tenant_id != tenant.id or connection.owner_subject_hash != subject.subject_hash:
        raise AgentNotFound()
    if connection.connection_type != WorkspaceConnection.TYPE_RUNTIME:
        raise AgentNotFound()
    device = ensure_runtime_connection(connection, operation="workspace.test")
    scope_capabilities = {
        "browser.control": "browser.v1",
        "command.execute": "terminal.v1",
        "tool.setup": "tool_setup.v1",
    }
    required_capabilities = {
        scope_capabilities.get(str(scope), "workspace.v1")
        for scope in (agent.workspace_capabilities or [])
    }
    missing = sorted(
        capability
        for capability in required_capabilities
        if int((device.capabilities or {}).get(capability) or 0) < 1
    )
    if missing:
        raise ComputerCapabilityUnavailable(
            "This Computer Runtime is missing: " + ", ".join(missing)
        )
    if is_default:
        AgentComputerBinding.objects.filter(
            tenant=tenant,
            agent=agent,
            caller_subject_hash=subject.subject_hash,
            is_default=True,
            status=SoftDeleteModel.STATUS_ACTIVE,
        ).update(is_default=False)
    binding, _ = AgentComputerBinding.objects.update_or_create(
        tenant=tenant,
        agent=agent,
        connection=connection,
        caller_subject_hash=subject.subject_hash,
        defaults={
            "project_id": getattr(request, "project_id", None) or None,
            "caller_principal_type": subject.principal_type,
            "is_default": is_default,
            "status": SoftDeleteModel.STATUS_ACTIVE,
            "deleted_at": None,
        },
    )
    return binding


@transaction.atomic
def delete_computer_binding(*, request, agent_id: str, binding_id: str) -> None:
    binding = list_computer_bindings(request=request, agent_id=agent_id).filter(id=binding_id).first()
    if binding is None:
        raise AgentNotFound()
    binding.delete()


def _get_bindable_agent(*, request, agent_id: str) -> Agent:
    return invoke_agent_policy("_get_bindable_agent", request=request, agent_id=agent_id)


def agent_capabilities(*, request) -> dict[str, bool]:
    tenant = get_tenant_from_request(request)
    can_admin = has_nexus_permission(request.user, tenant, "admin")
    return {
        "view": True,
        "create": can_admin,
        "manage": can_admin,
    }


@transaction.atomic
def update_agent(*, request, agent_id: str, data: dict[str, Any]) -> Agent:
    visible_agent = get_mutable_agent(request=request, agent_id=agent_id)
    invoke_agent_policy("validate_agent_update", request=request, data=data)
    agent = Agent.objects.select_for_update().get(id=visible_agent.id)
    update_fields: list[str] = []
    if (
        {"computer_requirement", "workspace_capabilities"}.intersection(data)
        and EdgeAgentRegistration.objects.filter(
            agent=agent,
            binding_mode=EdgeAgentRegistration.BINDING_MANAGED,
        ).exclude(computer_requirement="").exists()
    ):
        raise exceptions.ValidationError({
            "computer_requirement": "Computer access is declared by this managed Agent's SDK manifest."
        })
    if "name" in data:
        agent.name = validate_agent_name(str(data["name"]))
        update_fields.append("name")
    if "computer_requirement" in data:
        agent.computer_requirement = data["computer_requirement"]
        update_fields.append("computer_requirement")
    if "workspace_capabilities" in data:
        from .device_contracts import normalize_workspace_capabilities

        agent.workspace_capabilities = normalize_workspace_capabilities(data["workspace_capabilities"])
        update_fields.append("workspace_capabilities")
    mobile_fields = {"mobile_requirement", "mobile_capabilities"}.intersection(data)
    mobile_policy_source = data.get("mobile_policy_source")
    if mobile_policy_source == Agent.MOBILE_POLICY_SDK and mobile_fields:
        raise exceptions.ValidationError({
            "mobile_policy_source": "Restore SDK defaults separately from manual Mobile changes."
        })
    if mobile_policy_source is not None or mobile_fields:
        from .mobile_access import apply_mobile_policy, sdk_mobile_declaration

        if mobile_policy_source == Agent.MOBILE_POLICY_SDK:
            registration = EdgeAgentRegistration.objects.filter(
                agent=agent,
                binding_mode=EdgeAgentRegistration.BINDING_MANAGED,
            ).first()
            if registration is None:
                raise exceptions.ValidationError({
                    "mobile_policy_source": "Only a managed OpenWrt Agent can restore SDK defaults."
                })
            mobile_requirement, mobile_capabilities = sdk_mobile_declaration(registration)
            next_source = Agent.MOBILE_POLICY_SDK
        else:
            mobile_requirement = data.get("mobile_requirement", agent.mobile_requirement)
            mobile_capabilities = data.get("mobile_capabilities", agent.mobile_capabilities)
            next_source = Agent.MOBILE_POLICY_CLOUD
        apply_mobile_policy(
            agent=agent,
            requirement=mobile_requirement,
            capabilities=mobile_capabilities,
            source=next_source,
        )
        update_fields.extend(["mobile_requirement", "mobile_capabilities", "mobile_policy_source"])
    if "status" in data:
        next_status = str(data["status"])
        if next_status == Agent.STATUS_ACTIVE and not agent_has_runtime_target(agent):
            raise exceptions.ValidationError("Configure a Docker image or bind an OpenWrt Agent before enabling this agent.")
        agent.status = next_status
        update_fields.append("status")
        if next_status in {Agent.STATUS_DISABLED, Agent.STATUS_ARCHIVED}:
            agent.publication_status = Agent.PUBLICATION_SUSPENDED
            update_fields.append("publication_status")
    if "project_id" in data or "team_id" in data:
        project_id = data.get("project_id")
        team_id = data.get("team_id")
        if project_id and team_id:
            raise exceptions.ValidationError("Select either a project or a team, not both.")
        project = None
        team = None
        if project_id:
            project = Project.objects.filter(
                tenant=agent.tenant,
                id=project_id,
                status=SoftDeleteModel.STATUS_ACTIVE,
            ).first()
            if project is None:
                raise exceptions.NotFound("Project not found.")
        if team_id:
            team = Team.objects.filter(
                tenant=agent.tenant,
                id=team_id,
                status=SoftDeleteModel.STATUS_ACTIVE,
            ).first()
            if team is None:
                raise exceptions.NotFound("Team not found.")
        if agent.runtime_deployments.filter(
            status__in=[AgentRuntimeDeployment.STATUS_ACTIVE, AgentRuntimeDeployment.STATUS_DEPLOYING]
        ).exists():
            raise exceptions.ValidationError("Stop the active runtime before transferring this agent.")
        agent.project = project
        agent.team = team
        update_fields.extend(["project", "team"])
    if update_fields:
        agent.save(update_fields=[*dict.fromkeys(update_fields), "updated_at"])
        log_write(request=request, action="agents.update", agent=agent, metadata={"fields": update_fields})
        AgentLog.objects.create(agent=agent, message=f"Agent settings updated: {', '.join(dict.fromkeys(update_fields))}.")
    return agent


@transaction.atomic
def clone_agent(*, request, agent_id: str, name: str = "") -> Agent:
    source = get_mutable_agent(request=request, agent_id=agent_id)
    from apps.common.resource_limits import enforce_capability

    Tenant.objects.select_for_update(no_key=True).get(pk=source.tenant_id)
    enforce_capability(tenant=source.tenant, code="agents.agents")
    clone_name = validate_agent_name(name or unique_clone_name(source))
    clone = Agent.objects.create(
        tenant=source.tenant,
        project=source.project,
        team=source.team,
        name=clone_name,
        status=Agent.STATUS_DRAFT,
        visibility=Agent.VISIBILITY_PRIVATE,
        publication_status=Agent.PUBLICATION_UNPUBLISHED,
        repo_metadata=dict(source.repo_metadata or {}),
        created_by=request.user,
    )
    invoke_agent_policy("clone_commercial_configuration", source=source, clone=clone)
    try:
        resources = source.resource_config
    except AgentResourceConfig.DoesNotExist:
        resources = None
    if resources is not None:
        AgentResourceConfig.objects.create(agent=clone, cpu=resources.cpu, memory=resources.memory)
    log_write(
        request=request,
        action="agents.clone",
        agent=clone,
        metadata={"source_agent_id": str(source.id)},
    )
    AgentLog.objects.create(agent=clone, message=f"Agent cloned from {source.name} as a private draft.")
    return clone


@transaction.atomic
def delete_agent(*, request, agent_id: str) -> None:
    agent = get_mutable_agent(request=request, agent_id=agent_id)
    if agent.runtime_deployments.filter(
        status__in=[AgentRuntimeDeployment.STATUS_ACTIVE, AgentRuntimeDeployment.STATUS_DEPLOYING]
    ).exists():
        raise exceptions.ValidationError("Stop the active runtime before deleting this agent.")
    if agent.runtime_deployments.filter(runtime_kind="docker", docker_lifecycle__desired="running").exists():
        raise exceptions.ValidationError("Stop the Docker deployment before deletion so pending container recovery and cleanup are safely cancelled.")
    agent.status = SoftDeleteModel.STATUS_DELETED
    agent.publication_status = Agent.PUBLICATION_SUSPENDED
    agent.save(update_fields=["status", "publication_status", "updated_at"])
    log_write(request=request, action="agents.delete", agent=agent)
    AgentLog.objects.create(agent=agent, message="Agent deleted.")


def set_publication(*, request, agent_id: str, action: str) -> Agent:
    return invoke_agent_policy("set_publication", request=request, agent_id=agent_id, action=action)


def unique_clone_name(agent: Agent) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]", "-", f"{agent.name}-copy")[:58].strip("-") or "agent-copy"
    candidate = base
    counter = 2
    while Agent.objects.filter(tenant=agent.tenant, name=candidate).exclude(status=SoftDeleteModel.STATUS_DELETED).exists():
        suffix = f"-{counter}"
        candidate = f"{base[:64-len(suffix)]}{suffix}"
        counter += 1
    return candidate


def repo_init(*, request, agent_id: str) -> Agent:
    agent = get_mutable_agent(request=request, agent_id=agent_id)
    agent.repo_metadata = {
        "repo_id": f"repo_{agent.id}",
        "default_branch": "main",
        "initialized": True,
    }
    agent.save(update_fields=["repo_metadata", "updated_at"])
    log_write(request=request, action="agents.repo.init", agent=agent)
    return agent


def repo_push(*, request, agent_id: str, uploaded_file=None, commit_id: str = "") -> Agent:
    agent = get_mutable_agent(request=request, agent_id=agent_id)
    metadata = dict(agent.repo_metadata or {})
    metadata["last_push_id"] = uuid.uuid4().hex
    metadata["commit_id"] = commit_id or uuid.uuid4().hex[:12]
    if uploaded_file is not None:
        metadata["artifact_name"] = Path(uploaded_file.name).name
        metadata["artifact_size"] = getattr(uploaded_file, "size", 0)
    agent.repo_metadata = metadata
    agent.save(update_fields=["repo_metadata", "updated_at"])
    AgentLog.objects.create(agent=agent, message=f"Repository pushed: {metadata['commit_id']}")
    log_write(request=request, action="agents.repo.push", agent=agent, metadata={"commit_id": metadata["commit_id"]})
    return agent


def list_versions(*, request, agent_id: str):
    agent = get_agent(request=request, agent_id=agent_id)
    return agent.versions.order_by("-created_at")


def publish_version(*, request, agent_id: str, release_notes: str = "") -> AgentVersion:
    agent = get_mutable_agent(request=request, agent_id=agent_id)
    next_number = agent.versions.count() + 1
    artifact_metadata = dict(agent.repo_metadata or {})
    artifact_metadata.pop("rating", None)
    artifact_metadata.pop("review_count", None)
    artifact_metadata["release_notes"] = str(release_notes or "").strip()
    version = AgentVersion.objects.create(
        agent=agent,
        version=f"v{next_number}",
        commit_id=(agent.repo_metadata or {}).get("commit_id", ""),
        artifact_metadata=artifact_metadata,
        workspace_capabilities=list(agent.workspace_capabilities or []),
        mobile_requirement=agent.mobile_requirement,
        mobile_capabilities=list(agent.mobile_capabilities or []),
        created_by=request.user,
    )
    agent.current_version = version.version
    agent.save(update_fields=["current_version", "updated_at"])
    AgentLog.objects.create(agent=agent, message=f"Published version {version.version}.")
    log_write(request=request, action="agents.version.publish", agent=agent, metadata={"version": version.version})
    return version


def rollback_version(*, request, agent_id: str, version: str) -> AgentVersion:
    agent = get_mutable_agent(request=request, agent_id=agent_id)
    target = agent.versions.filter(version=version).first()
    if target is None:
        raise exceptions.NotFound("Agent version not found.")
    agent.current_version = target.version
    agent.save(update_fields=["current_version", "updated_at"])
    AgentLog.objects.create(agent=agent, message=f"Rolled back to {target.version}.")
    log_write(request=request, action="agents.version.rollback", agent=agent, metadata={"version": target.version})
    return target


def deploy_agent(*, request, agent_id: str, env: str) -> AgentDeployment:
    return invoke_agent_policy("deploy_agent", request=request, agent_id=agent_id, env=env)


def stop_agent(*, request, agent_id: str, env: str) -> AgentDeployment:
    agent = get_mutable_agent(request=request, agent_id=agent_id)
    deployment = agent.deployments.filter(env=env).first()
    if deployment is None:
        raise exceptions.NotFound("Agent deployment not found.")
    deployment.status = AgentDeployment.STATUS_STOPPED
    deployment.save(update_fields=["status", "updated_at"])
    AgentLog.objects.create(agent=agent, deployment=deployment, message=f"Deployment {env} stopped.")
    log_write(request=request, action="agents.stop", agent=agent, metadata={"env": env})
    return deployment


def get_logs(*, request, agent_id: str, tail: int = 100):
    agent = get_agent(request=request, agent_id=agent_id)
    tail = min(max(tail, 1), 100)
    return agent.logs.order_by("-created_at")[:tail]


def get_status(*, request, agent_id: str) -> dict[str, Any]:
    agent = get_agent(request=request, agent_id=agent_id)
    deployments = list(agent.deployments.order_by("env"))
    return {"agent": agent, "deployments": deployments}


def list_display_runs(*, request, agent_id: str):
    from .observability import optimized_runs
    agent = get_agent(request=request, agent_id=agent_id)
    require_agent_observability(request=request, agent=agent)
    return optimized_runs(agent.display_runs.all()).order_by("-created_at", "-pk")[:100]


def list_observability_events(*, request, agent_id: str, run_id: str, cursor: int = 0, limit: int = 200):
    agent = get_agent(request=request, agent_id=agent_id)
    require_agent_observability(request=request, agent=agent)
    run = agent.display_runs.filter(id=run_id).first()
    if run is None:
        raise AgentNotFound()
    return observability_events_queryset(run.events.filter(seq__gt=max(cursor, 0))).order_by("seq")[: min(max(limit, 1), 500)]


def observability_events_queryset(queryset):
    # Caller interaction content belongs to the private Display. Developer
    # observability must not expose chat or Mobile approval prompts/payloads.
    return (
        queryset.exclude(
            event_type__in={
                "TEXT_MESSAGE_START",
                "TEXT_MESSAGE_CONTENT",
                "TEXT_MESSAGE_END",
                "TEXT_MESSAGE_CHUNK",
                "MESSAGES_SNAPSHOT",
            }
        )
        .exclude(
            event_type="CUSTOM",
            payload_json__name__in={
                "nexus.chat.input_required",
                "nexus.display.title",
                AGUI_CUSTOM_COMPUTER_FRAME,
                "nexus.mobile.input_required",
            },
        )
    )


def get_private_display_run(*, request, run_id: str, require_token: bool = True) -> AgentDisplayRun:
    tenant = get_tenant_from_request(request)
    subject = request_subject(request)
    run = (
        AgentDisplayRun.objects.filter(
            id=run_id,
            run_kind=AgentDisplayRun.KIND_INVOCATION,
            consumer_tenant=tenant,
            consumer_project_id=getattr(request, "project_id", None) or None,
            caller_subject_hash=subject.subject_hash,
            caller_hidden_at__isnull=True,
        )
        .select_related(
            "agent",
            "runtime",
            "computer_binding",
            "mobile_binding",
            "mobile_binding__device",
            "execution_task",
        )
        .first()
    )
    token = str(request.headers.get("X-Nexus-Agent-Display-Token") or "")
    token_valid = bool(
        run
        and token
        and run.display_token_hash
        and hmac.compare_digest(run.display_token_hash, hash_token(token))
        and run.display_token_expires_at
        and run.display_token_expires_at > timezone.now()
    )
    if run and token and not token_valid:
        from django.core import signing
        try:
            proof = signing.loads(token, salt="agent-private-display-v2", max_age=3600)
            token_valid = proof.get("run") == str(run.id) and proof.get("subject") == subject.subject_hash
        except (signing.BadSignature, TypeError, ValueError, AttributeError):
            token_valid = False
    if run is None or (require_token and not token_valid):
        raise AgentNotFound()
    return run


@transaction.atomic
def issue_private_display_token(*, request, run_id: str) -> tuple[AgentDisplayRun, str]:
    run = get_private_display_run(request=request, run_id=run_id, require_token=False)
    from django.core import signing
    # Renewal in one tab must not revoke another authenticated caller tab.
    token = signing.dumps({"run": str(run.id), "subject": run.caller_subject_hash,
        "nonce": secrets.token_urlsafe(32)}, salt="agent-private-display-v2")
    run.display_token_hash = hash_token(token)
    run.display_token_expires_at = timezone.now() + timedelta(hours=1)
    run.save(update_fields=["display_token_hash", "display_token_expires_at", "updated_at"])
    return run, token


def private_display_payload(*, run: AgentDisplayRun) -> dict[str, Any]:
    from .context_extension import run_presentation_fields
    from .run_history import completion_unread
    from .computer_switching import computer_payload
    from .follow_ups import payload as follow_up_payload
    from .run_failures import failure_payload
    terminal = getattr(run, "terminal_session", None)
    interactions = [private_interaction_payload(item) for item in reversed(list(run.interactions.order_by("-created_at")[:100]))]
    task = getattr(run, "execution_task", None)
    invocation = run.runtime_invocations.order_by("-turn_index", "-created_at").first()
    usage = run.model_usage.filter(primary=True).order_by("-turn_index", "-created_at").first()
    usage_totals = run.model_usage.aggregate(
        input_tokens=Sum("input_tokens"), output_tokens=Sum("output_tokens"),
        cached_input_tokens=Sum("cached_input_tokens"), reasoning_tokens=Sum("reasoning_tokens"),
    )
    tool_name = task.tool_name if task else _private_display_tool_name(run)
    descriptor = next(
        (
            item
            for item in interaction_tools(agent=run.agent, runtime=run.runtime)
            if item["name"] == tool_name
        ),
        None,
    )
    descriptor_mode = str(descriptor["display_mode"]) if descriptor else ""
    turn_index = max(int((getattr(task, "request_json", {}) or {}).get("turn_index") or 1), 1)
    project_snapshot = dict(run.project_context_snapshot or {})
    display_mode = (
        "task"
        if task or run.interaction_mode == "task" or descriptor_mode == "task"
        else "interactive"
        if interactions or descriptor_mode == "interactive"
        else "tool"
    )
    return {
        **public_display_run(run),
        "completion_unread": completion_unread(run),
        "agent_name": run.agent.name,
        "display_title": run.display_title,
        "display_title_source": run.display_title_source,
        "tool_name": tool_name,
        "turn_index": turn_index,
        "display_mode": display_mode,
        "tool_policy": dict(descriptor["policy"]) if descriptor else {},
        "follow_up": follow_up_payload(run),
        "failure": failure_payload(run),
        **run_presentation_fields(invocation=invocation),
        "execution": {
            "profile_id": invocation.execution_profile_id if invocation else "",
            "model": invocation.execution_model if invocation else "",
            "reasoning_effort": invocation.reasoning_effort if invocation else "",
        },
        "project_context": {
            "project_id": str(project_snapshot.get("project_id") or ""),
            "project_name": str(project_snapshot.get("project_name") or ""),
            "instructions_markdown": str(project_snapshot.get("instructions_markdown") or ""),
            "revision": int(project_snapshot.get("instructions_revision") or 0),
            "captured_at": project_snapshot.get("captured_at"),
        },
        "usage": {
            "reported": usage is not None,
            "current": ({
                "turn_index": usage.turn_index,
                "profile_id": usage.profile_id,
                "model": usage.model,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cached_input_tokens": usage.cached_input_tokens,
                "reasoning_tokens": usage.reasoning_tokens,
                "context_window": usage.context_window,
                "source": usage.source,
            } if usage else None),
            "totals": {key: int(value or 0) for key, value in usage_totals.items()},
        },
        "execution_task": {
            "status": task.status,
            "execution_state": getattr(getattr(task, "execution", None), "state", "legacy"),
            "continuable": task.continuable,
            "recovery": {
                "managed": task.recovery_protocol >= 1,
                "protocol": task.recovery_protocol,
                "attempt": task.retry_count,
                "state": getattr(getattr(task, "execution", None), "state", "limited"),
                "last_committed_operation": (
                    task.operations.filter(status="succeeded")
                    .order_by("-turn_index", "-sequence")
                    .values_list("sequence", flat=True)
                    .first()
                ),
            },
            "retry_count": task.retry_count,
            "error_code": task.error_code,
            "result": dict(task.result_json or {}),
            "expires_at": task.expires_at,
        }
        if task
        else None,
        "computer": {
            **computer_payload(run),
            "attached": bool(run.computer_binding_id),
            "terminal_status": terminal.status if terminal else "not_started",
            "viewer_mode": "read_only",
            "workspace_cwd": run.workspace_cwd or ".",
        },
        "mobile": {
            "attached": bool(run.mobile_binding_id),
            "name": run.mobile_binding.device.name if run.mobile_binding_id else "",
            "status": run.mobile_binding.device.lifecycle_status if run.mobile_binding_id else "unavailable",
            "capabilities": list(run.mobile_capabilities_snapshot or []),
            "commands": [
                {
                    "id": str(command.id),
                    "action": command.action,
                    "status": command.status,
                    "risk_level": command.risk_level,
                    "requires_approval": command.requires_approval,
                    "created_at": command.created_at,
                    "completed_at": command.completed_at,
                }
                for command in reversed(list(run.mobile_commands.order_by("-created_at")[:50]))
            ],
        },
        "interactions": interactions,
        "messages": [
            {
                "id": str(message.id),
                "sequence": message.sequence,
                "turn_index": message.turn_index,
                "role": message.role,
                "content": message.content,
                "content_blocks": message.content_blocks or [{"type": "markdown", "text": message.content}],
                "created_at": message.created_at,
            }
            for message in reversed(list(run.messages.order_by("-sequence")[:200]))
        ],
    }


def private_run_computer_directories(*, request, run_id: str, path: str = "") -> dict[str, Any]:
    from apps.workspaces.execution import list_workspace_files, resolve_workspace_relative_path

    run = get_private_display_run(request=request, run_id=run_id)
    if run.computer_binding_id is None or not run.workspace_root:
        raise AgentNotFound()
    relative_path = resolve_workspace_relative_path(path=path or run.workspace_cwd or ".")
    listing = list_workspace_files(
        connection=run.computer_binding.connection,
        root=run.workspace_root,
        path=relative_path,
    )
    directories = [
        {
            "name": str(item.get("name") or ""),
            "path": resolve_workspace_relative_path(path=str(item.get("relative_path") or item.get("name") or "")),
        }
        for item in listing.get("items", [])
        if item.get("type") == "directory"
    ]
    return {
        "cwd": run.workspace_cwd or ".",
        "path": relative_path,
        "parent": None if relative_path == "." else (posixpath.dirname(relative_path) or "."),
        "directories": directories,
    }


def update_private_run_workspace_cwd(*, request, run_id: str, path: str) -> dict[str, Any]:
    from apps.workspaces.execution import list_workspace_files, resolve_workspace_relative_path

    run = get_private_display_run(request=request, run_id=run_id)
    if run.computer_binding_id is None or not run.workspace_root:
        raise AgentNotFound()
    candidate = resolve_workspace_relative_path(path=path or ".")
    # Listing verifies that the target exists and is a directory on the caller's Computer.
    list_workspace_files(
        connection=run.computer_binding.connection,
        root=run.workspace_root,
        path=candidate,
    )
    # Runtime commands are only visible/woken after enqueue commits. Never wait
    # for the Computer while holding the transaction that contains its command.
    with transaction.atomic():
        locked = AgentDisplayRun.objects.select_for_update().get(id=run.id)
        # Authorization and the selected Computer may change during remote I/O.
        get_private_display_run(request=request, run_id=run_id)
        if (locked.computer_binding_id, locked.computer_revision, locked.workspace_root) != (
            run.computer_binding_id, run.computer_revision, run.workspace_root
        ):
            from .computer_switching import ComputerSwitchConflict
            raise ComputerSwitchConflict(
                "The Run Computer changed while checking the folder. Refresh and choose again.",
            )
        # Already-dispatched commands retain their original directory.
        locked.workspace_cwd = candidate
        locked.save(update_fields=["workspace_cwd", "updated_at"])
    return private_run_computer_directories(request=request, run_id=str(locked.id), path=candidate)


@transaction.atomic
def update_private_display_title(*, request, run_id: str, title: str) -> AgentDisplayRun:
    run = get_private_display_run(request=request, run_id=run_id)
    value = _display_title(title)
    if not value:
        raise exceptions.ValidationError({"title": "Title must be 1..80 characters."})
    locked = AgentDisplayRun.objects.select_for_update().get(id=run.id)
    locked.display_title = value
    locked.display_title_source = AgentDisplayRun.DISPLAY_TITLE_USER
    locked.save(update_fields=["display_title", "display_title_source", "updated_at"])
    return locked


@transaction.atomic
def hide_private_display_run(*, request, run_id: str) -> None:
    run = get_private_display_run(request=request, run_id=run_id)
    locked = AgentDisplayRun.objects.select_for_update().get(id=run.id)
    if locked.status == AgentDisplayRun.STATUS_RUNNING:
        from .runtime_services import AgentRuntimeOperationConflict
        raise AgentRuntimeOperationConflict("Stop this Run before removing it from history. Hiding a running Run does not release concurrent capacity.")
    locked.caller_hidden_at = timezone.now()
    locked.save(update_fields=["caller_hidden_at", "updated_at"])


def _private_display_tool_name(run: AgentDisplayRun) -> str:
    title = str(run.title or "").strip()
    prefix = f"{run.agent.name}:"
    if title.startswith(prefix):
        return title[len(prefix) :].strip()
    return ""


def private_interaction_payload(interaction: AgentRunInteraction) -> dict[str, Any]:
    return {
        "id": str(interaction.id),
        "key": interaction.key,
        "kind": interaction.kind,
        "prompt": interaction.prompt,
        "choices": list(interaction.choices_json or []),
        "response": dict(interaction.response_json or {}) if interaction.status == AgentRunInteraction.STATUS_ANSWERED else {},
        "status": interaction.status,
        "expires_at": interaction.expires_at,
        "answered_at": interaction.answered_at,
    }


@transaction.atomic
def answer_private_run_interaction(
    *, request, run_id: str, interaction_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    run = get_private_display_run(request=request, run_id=run_id)
    interaction = AgentRunInteraction.objects.select_for_update().filter(
        id=interaction_id,
        run=run,
        status=AgentRunInteraction.STATUS_PENDING,
    ).first()
    if interaction is None or run.status != AgentDisplayRun.STATUS_RUNNING:
        raise AgentNotFound()
    if interaction.expires_at <= timezone.now():
        interaction.status = AgentRunInteraction.STATUS_EXPIRED
        interaction.save(update_fields=["status", "updated_at"])
        raise AgentNotFound()
    text = str(data.get("text") or data.get("value") or "").strip()
    value = str(data.get("value") or text).strip()
    if not text or len(text) > 4000 or (interaction.choices_json and len(value) > 128):
        raise exceptions.ValidationError("A valid Chat reply is required.")
    allowed = {str(item.get("value") or "") for item in interaction.choices_json or [] if isinstance(item, dict)}
    if allowed and value not in allowed:
        raise exceptions.ValidationError("Select one of the available Chat choices.")
    interaction.response_json = {"value": value, "text": text}
    interaction.status = AgentRunInteraction.STATUS_ANSWERED
    interaction.answered_at = timezone.now()
    interaction.save(update_fields=["response_json", "status", "answered_at", "updated_at"])
    message_id = "user-" + uuid.uuid4().hex
    append_display_event(
        run=run,
        event_type="TEXT_MESSAGE_START",
        payload={"messageId": message_id, "role": "user"},
    )
    append_display_event(
        run=run,
        event_type="TEXT_MESSAGE_CONTENT",
        payload={"messageId": message_id, "delta": text},
    )
    append_display_event(
        run=run,
        event_type="TEXT_MESSAGE_END",
        payload={"messageId": message_id},
    )
    append_display_event(
        run=run,
        event_type="CUSTOM",
        payload={
            "name": "nexus.chat.status",
            "value": {"interaction_id": str(interaction.id), "status": "answered"},
        },
        visibility="private",
    )
    try:
        task = run.execution_task
    except AgentExecutionTask.DoesNotExist:
        task = None
    if task is not None and task.status == AgentExecutionTask.STATUS_INPUT_REQUIRED:
        task.status = AgentExecutionTask.STATUS_WORKING
        task.save(update_fields=["status", "updated_at"])
    return private_interaction_payload(interaction)


def get_private_display_asset(*, request, run_id: str, asset_id: str) -> AgentDisplayAsset:
    run = get_private_display_run(request=request, run_id=run_id)
    asset = run.display_assets.filter(id=asset_id).first()
    if asset is None:
        raise AgentNotFound()
    return asset


def issue_private_terminal_ticket(*, request, run_id: str) -> dict[str, Any]:
    run = get_private_display_run(request=request, run_id=run_id)
    session = getattr(run, "terminal_session", None)
    if session is None:
        raise exceptions.ValidationError("Agent terminal has not started.")
    ticket = secrets.token_urlsafe(32)
    cache.set(
        "agent-terminal-ticket:" + hash_token(ticket),
        {
            "run_id": str(run.id),
            "session_id": str(session.id),
            "caller_subject_hash": run.caller_subject_hash,
        },
        timeout=60,
    )
    return {
        "ticket": ticket,
        "expires_in": 60,
        "websocket_url": f"/ws/agent-runs/{run.id}/terminal/?ticket={ticket}",
    }


def list_output_artifacts(*, request, agent_id: str, run_id: str):
    agent = get_agent(request=request, agent_id=agent_id)
    require_agent_observability(request=request, agent=agent)
    run = agent.display_runs.filter(id=run_id).first()
    if run is None:
        raise exceptions.NotFound("Agent display run not found.")
    # Artifacts are materialized by event ingestion. Reads must not replay
    # history, perform filesystem I/O, or rewrite old turn attribution.
    artifacts = run.output_artifacts.exclude(status=SoftDeleteModel.STATUS_DELETED)
    if run.caller_subject_hash != request_subject(request).subject_hash:
        artifacts = artifacts.exclude(producer_step__startswith="display_image:")
    return artifacts.order_by("workspace_path")


def require_image_artifact_caller(*, request, artifact):
    # Publishing an Agent never grants access to another caller's generated
    # images. Archiving must be an explicit action by the producing caller.
    if artifact.producer_step.startswith("display_image:") and (
        not artifact.run.caller_subject_hash or artifact.run.caller_subject_hash != request_subject(request).subject_hash
    ):
        raise exceptions.NotFound("Agent output artifact not found.")


@transaction.atomic
def scan_output_artifact(*, request, agent_id: str, run_id: str, artifact_id: str) -> AgentOutputArtifact:
    from apps.workspaces.execution import read_workspace_file
    from apps.datasets.storage_backends import get_dataset_storage_backend

    agent = get_mutable_agent(request=request, agent_id=agent_id)
    run = agent.display_runs.select_related("runtime", "runtime__workspace_connection").filter(id=run_id).first()
    if run is None:
        raise exceptions.NotFound("Agent display run not found.")
    if run.status != AgentDisplayRun.STATUS_COMPLETED:
        raise exceptions.ValidationError("Agent display run must be completed before output scan.")
    artifact = (
        run.output_artifacts.select_for_update()
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .filter(id=artifact_id)
        .first()
    )
    if artifact is None:
        raise exceptions.NotFound("Agent output artifact not found.")
    require_image_artifact_caller(request=request, artifact=artifact)
    runtime = run.runtime
    scan_error = ""
    if run.run_kind == AgentDisplayRun.KIND_INVOCATION:
        if artifact.snapshot_status != "ready" or not artifact.snapshot_object_key:
            raise exceptions.ValidationError("Agent output snapshot is unavailable.")
        stream = get_dataset_storage_backend(artifact.snapshot_storage_backend).open(object_key=artifact.snapshot_object_key)
        try:
            from apps.datasets.file_scanning import scan_asset_stream
            stats = scan_asset_stream(stream, file_name=artifact.original_file_name, content_type=artifact.content_type)
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        size_bytes, content_hash = stats["size_bytes"], stats["sha256"]
        scan_error = stats["error_code"]
        if stats.get("complete", True) and (size_bytes != artifact.size_bytes or (artifact.sha256 and content_hash != artifact.sha256)):
            scan_error = "SNAPSHOT_INTEGRITY_MISMATCH"
    else:
        if runtime is None or runtime.workspace_connection is None:
            raise exceptions.ValidationError("Agent run is not attached to a legacy runtime workspace.")
        result = read_workspace_file(
            connection=runtime.workspace_connection,
            root=runtime.workspace_root or ".",
            path=artifact.workspace_path,
        )
        content = str(result.get("content") or "")
        content_bytes = content.encode("utf-8")
        size_bytes, content_hash = len(content_bytes), hashlib.sha256(content_bytes).hexdigest()
        _, stats = redact_payload(content)
    content_type = artifact.content_type or mimetypes.guess_type(artifact.original_file_name)[0] or "text/plain"
    finding_count = stats["replacement_count"] + stats["sensitive_key_count"]
    passed = finding_count == 0 and not scan_error

    artifact.content_type = content_type
    # Invocation snapshot identity is immutable; never bless a changed object by
    # replacing its authoritative digest during a scan.
    if run.run_kind != AgentDisplayRun.KIND_INVOCATION:
        artifact.size_bytes = size_bytes
        artifact.sha256 = content_hash
    artifact.scan_status = AgentOutputArtifact.SCAN_PASSED if passed else AgentOutputArtifact.SCAN_FAILED
    artifact.policy_status = AgentOutputArtifact.POLICY_APPROVED if passed else AgentOutputArtifact.POLICY_BLOCKED
    artifact.scan_metadata = {
        "pipeline": "rules_stream_v2" if run.run_kind == AgentDisplayRun.KIND_INVOCATION else "rules_v1",
        "error_code": scan_error,
        "finding_count": finding_count,
        "replacement_count": stats["replacement_count"],
        "sensitive_key_count": stats["sensitive_key_count"],
        "text_scanned": is_text_output(content_type, artifact.original_file_name),
        "scanned_at": timezone.now().isoformat(),
        "scope": stats.get("scope", "text_secret_rules"),
        **{key: stats[key] for key in ("width", "height") if key in stats},
    }
    artifact.save(
        update_fields=[
            "content_type",
            "size_bytes",
            "sha256",
            "scan_status",
            "policy_status",
            "scan_metadata",
            "updated_at",
        ]
    )
    log_write(
        request=request,
        action="agents.output_artifact.scan",
        agent=agent,
        metadata={
            "run_id": str(run.id),
            "artifact_id": str(artifact.id),
            "workspace_path": artifact.workspace_path,
            "scan_status": artifact.scan_status,
            "policy_status": artifact.policy_status,
            "finding_count": finding_count,
        },
    )
    return artifact


@transaction.atomic
def redact_display_run(*, request, agent_id: str, run_id: str) -> AgentDisplayRun:
    agent = get_mutable_agent(request=request, agent_id=agent_id)
    run = agent.display_runs.filter(id=run_id).first()
    if run is None:
        raise exceptions.NotFound("Agent display run not found.")
    if run.status != AgentDisplayRun.STATUS_COMPLETED:
        raise exceptions.ValidationError("Agent display run must be completed before redaction.")
    events = run.events.order_by("seq")
    if not events.exists():
        raise exceptions.ValidationError("Agent display run has no events to redact.")

    total_replacements = 0
    sensitive_key_hits = 0
    for event in events.iterator(chunk_size=20):
        redacted_payload, stats = redact_payload(event.payload_json)
        total_replacements += stats["replacement_count"]
        sensitive_key_hits += stats["sensitive_key_count"]
        event.redacted_payload_json = redacted_payload
        event.redaction_metadata = {
            "pipeline": "rules_v1",
            "replacement_count": stats["replacement_count"],
            "sensitive_key_count": stats["sensitive_key_count"],
            "redacted_at": timezone.now().isoformat(),
        }
        event.save(update_fields=["redacted_payload_json", "redaction_metadata"])

    run.redaction_status = AgentDisplayRun.REDACTION_PASSED
    run.redaction_metadata = {
        "pipeline": "rules_v1",
        "event_count": events.count(),
        "replacement_count": total_replacements,
        "sensitive_key_count": sensitive_key_hits,
        "redacted_at": timezone.now().isoformat(),
    }
    run.save(update_fields=["redaction_status", "redaction_metadata", "updated_at"])
    log_write(request=request, action="agents.display_run.redact", agent=agent, metadata={"run_id": str(run.id), **run.redaction_metadata})
    return run


from apps.common.redaction import redact_payload


def list_memory_items(*, request, agent_id: str):
    agent = get_agent(request=request, agent_id=agent_id)
    require_agent_observability(request=request, agent=agent)
    return agent.memory_items.exclude(status=SoftDeleteModel.STATUS_DELETED).order_by("-created_at")


def require_agent_observability(*, request, agent: Agent) -> None:
    if not has_nexus_permission(
        request.user,
        agent.tenant,
        "agent.observability.read",
        resource_type="agent",
        resource_id=str(agent.id),
    ):
        raise AgentNotFound()


@transaction.atomic
def create_memory_item(*, request, agent_id: str, data: dict[str, Any]) -> AgentMemoryItem:
    agent = get_mutable_agent(request=request, agent_id=agent_id)
    source_run = None
    source_run_id = data.get("source_run_id")
    if source_run_id:
        source_run = AgentDisplayRun.objects.filter(tenant=agent.tenant, agent=agent, id=source_run_id).first()
        if source_run is None:
            raise exceptions.NotFound("Agent display run not found.")
    item = AgentMemoryItem.objects.create(
        tenant=agent.tenant,
        project=agent.project,
        agent=agent,
        actor=request.user,
        memory_type=data.get("memory_type") or AgentMemoryItem.TYPE_FACT,
        scope=data.get("scope") or AgentMemoryItem.SCOPE_DEVELOPER_ONLY,
        content_text=data.get("content_text", ""),
        content_json=data.get("content_json") or {},
        source_run=source_run,
        source_event_ids=[str(value) for value in data.get("source_event_ids", [])],
        confidence=data.get("confidence"),
        sensitivity_level=data.get("sensitivity_level") or AgentMemoryItem.SENSITIVITY_INTERNAL,
        consent_status=data.get("consent_status") or AgentMemoryItem.CONSENT_PENDING,
        license_status=data.get("license_status") or AgentMemoryItem.LICENSE_UNKNOWN,
    )
    log_write(
        request=request,
        action="agents.memory.create",
        agent=agent,
        metadata={
            "memory_item_id": str(item.id),
            "memory_type": item.memory_type,
            "sensitivity_level": item.sensitivity_level,
            "consent_status": item.consent_status,
            "license_status": item.license_status,
        },
    )
    return item


def export_mcp(*, request, agent_id: str) -> dict[str, Any]:
    consumer_tenant = get_tenant_from_request(request)
    candidate = Agent.objects.filter(id=agent_id).exclude(status=SoftDeleteModel.STATUS_DELETED).first()
    if candidate is None:
        raise AgentNotFound()
    agent = (
        get_agent(request=request, agent_id=agent_id)
        if candidate.tenant_id == consumer_tenant.id
        else _get_bindable_agent(request=request, agent_id=agent_id)
    )
    return build_agent_mcp_export(
        request=request,
        agent=agent,
        consumer_tenant_id=str(consumer_tenant.id),
        consumer_project_id=str(getattr(request, "project_id", "") or "") or None,
    )


def create_agent_key(*, request, agent_id: str) -> tuple[Any, str]:
    return invoke_agent_policy("create_agent_key", request=request, agent_id=agent_id)


def set_visibility(*, request, agent_id: str, visibility: str, project_id=None) -> Agent:
    return invoke_agent_policy("set_visibility", request=request, agent_id=agent_id, visibility=visibility, project_id=project_id)


def set_pricing(
    *,
    request,
    agent_id: str,
    pricing_type: str,
    price: Decimal,
    max_price: Decimal = Decimal("0"),
    currency: str = "USD",
    tool_limits: list[dict[str, Any]] | None = None,
) -> AgentPricing:
    return invoke_agent_policy("set_pricing", request=request, agent_id=agent_id, pricing_type=pricing_type, price=price, max_price=max_price, currency=currency, tool_limits=tool_limits)


def set_resources(*, request, agent_id: str, cpu: str, memory: str) -> AgentResourceConfig:
    agent = get_mutable_agent(request=request, agent_id=agent_id)
    from apps.common.resource_limits import enforce_capability
    from .docker_policy import resource_limits
    from types import SimpleNamespace

    limits = resource_limits(SimpleNamespace(agent=SimpleNamespace(resource_config=SimpleNamespace(cpu=cpu, memory=memory))))

    # Enforce exactly what the Docker runner will allocate, including its
    # supported G/M suffixes, rather than parsing the same value differently.
    enforce_capability(tenant=agent.tenant, code="agents.hosted_cpu", requested=Decimal(limits["cpu"]))
    enforce_capability(tenant=agent.tenant, code="agents.hosted_memory_mb", requested=Decimal(limits["memory_bytes"]) / (1024 ** 2))
    config, _ = AgentResourceConfig.objects.update_or_create(
        agent=agent,
        defaults={"cpu": cpu, "memory": memory},
    )
    log_write(request=request, action="agents.resources.set", agent=agent, metadata={"cpu": cpu, "memory": memory})
    AgentLog.objects.create(agent=agent, message=f"Runtime resources updated to {cpu} CPU and {memory} memory.")
    return config


def get_mutable_agent(*, request, agent_id: str) -> Agent:
    agent = get_agent(request=request, agent_id=agent_id)
    if not has_nexus_permission(request.user, agent.tenant, "admin", resource_type="agent", resource_id=str(agent.id)):
        raise exceptions.PermissionDenied("Agent admin permission is required.")
    return agent


def visible_agents(*, user, tenant: Tenant):
    return invoke_agent_policy("visible_agents", user=user, tenant=tenant)


def list_marketplace_agents(*, request):
    return invoke_agent_policy("list_marketplace_agents", request=request)


def get_marketplace_agent(*, agent_id: str) -> Agent:
    return invoke_agent_policy("get_marketplace_agent", agent_id=agent_id)


def get_public_agent_display(*, request, agent_id: str) -> dict[str, Any]:
    return invoke_agent_policy("get_public_agent_display", request=request, agent_id=agent_id)


def get_display_agent(*, agent_id: str) -> Agent:
    return invoke_agent_policy("get_display_agent", agent_id=agent_id)


class EmptyQueryRequest:
    query_params: dict[str, str] = {}


def public_agent_summary(agent: Agent) -> dict[str, Any]:
    return invoke_agent_policy("public_agent_summary", agent=agent)


@transaction.atomic
def append_display_event(
    *,
    run: AgentDisplayRun,
    event_type: str,
    payload: dict[str, Any],
    visibility: str = "public",
    client_event_id: str | None = None,
) -> AgentDisplayEvent:
    locked = AgentDisplayRun.objects.select_for_update().get(id=run.id)
    if normalize_agui_event_type(event_type) in {
        AgentDisplayEvent.TYPE_RUN_STARTED,
        AgentDisplayEvent.TYPE_RUN_COMPLETED,
    }:
        payload = {
            **payload,
            "threadId": payload.get("threadId") or str(locked.agent_id),
            "runId": payload.get("runId") or str(locked.id),
        }
    agui_event = normalize_agui_event(event_type=event_type, payload=payload)
    if agui_event.get("name") == AGUI_CUSTOM_COMPUTER_FRAME:
        validate_frame_run_scope(agui_event["value"], run=locked)
    sanitized_payload = sanitize_display_payload(agui_event)
    normalized_event_id = str(client_event_id or "").strip() or None
    if normalized_event_id and len(normalized_event_id) > 128:
        raise exceptions.ValidationError({"event_id": ["Ensure this field has no more than 128 characters."]})
    if normalized_event_id:
        existing = locked.events.filter(client_event_id=normalized_event_id).first()
        if existing is not None:
            if (
                existing.event_type == agui_event["type"]
                and existing.payload_json == sanitized_payload
                and existing.visibility == visibility
            ):
                return existing
            raise AgentEventConflict()
    if locked.run_kind == AgentDisplayRun.KIND_INVOCATION and locked.status != AgentDisplayRun.STATUS_RUNNING:
        raise exceptions.ValidationError("Agent invocation Run is already closed.")
    last_seq = locked.events.order_by("-seq").values_list("seq", flat=True).first() or 0
    event = AgentDisplayEvent.objects.create(
        tenant=locked.tenant,
        agent=locked.agent,
        run=locked,
        seq=last_seq + 1,
        client_event_id=normalized_event_id,
        event_type=agui_event["type"],
        payload_json=sanitized_payload,
        visibility=visibility,
    )
    sync_output_artifact_from_event(event=event)
    sync_memory_item_from_event(event=event)
    sync_run_message_from_event(event=event)
    if event.event_type == "CUSTOM" and event.visibility == "public" and (event.payload_json or {}).get("name") == "nexus.image.created":
        materialize_private_run_result(run=locked, value=(event.payload_json or {}).get("value", {}))
    sync_private_display_title_from_event(event=event)
    if agui_event["type"] == AgentDisplayEvent.TYPE_RUN_COMPLETED:
        locked.status = AgentDisplayRun.STATUS_COMPLETED
        locked.completed_at = timezone.now()
        locked.save(update_fields=["status", "completed_at", "updated_at"])
    elif agui_event["type"] == AgentDisplayEvent.TYPE_RUN_FAILED:
        locked.status = AgentDisplayRun.STATUS_FAILED
        locked.completed_at = timezone.now()
        locked.save(update_fields=["status", "completed_at", "updated_at"])
    return event


def _display_title(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:80]


def sync_private_display_title_from_event(*, event: AgentDisplayEvent) -> AgentDisplayRun:
    run = event.run
    if run.run_kind != AgentDisplayRun.KIND_INVOCATION:
        return run
    if run.display_title_source == AgentDisplayRun.DISPLAY_TITLE_USER:
        return run
    title = ""
    source = ""
    payload = event.payload_json or {}
    if event.event_type == "CUSTOM" and payload.get("name") == "nexus.display.title":
        value = payload.get("value") if isinstance(payload.get("value"), dict) else {}
        title = _display_title(value.get("title"))
        source = AgentDisplayRun.DISPLAY_TITLE_AGENT
    elif event.event_type == "TEXT_MESSAGE_END" and event.visibility == "public":
        message = sync_run_message_from_event(event=event)
        if message is not None and message.role == AgentRunMessage.ROLE_ASSISTANT:
            title = _display_title(message.content)
            source = AgentDisplayRun.DISPLAY_TITLE_DERIVED
    if not title:
        return run
    locked = AgentDisplayRun.objects.select_for_update().get(id=run.id)
    if locked.display_title_source == AgentDisplayRun.DISPLAY_TITLE_USER:
        return locked
    if source == AgentDisplayRun.DISPLAY_TITLE_AGENT and locked.display_title_source == AgentDisplayRun.DISPLAY_TITLE_AGENT:
        return locked
    if source == AgentDisplayRun.DISPLAY_TITLE_DERIVED and locked.display_title_source != AgentDisplayRun.DISPLAY_TITLE_PENDING:
        return locked
    locked.display_title = title
    locked.display_title_source = source
    locked.save(update_fields=["display_title", "display_title_source", "updated_at"])
    return locked


def sync_run_message_from_event(*, event: AgentDisplayEvent) -> AgentRunMessage | None:
    """Persist a completed caller-visible message on its Run."""

    if event.event_type != "TEXT_MESSAGE_END" or event.visibility != "public":
        return None
    payload = event.payload_json or {}
    message_id = str(payload.get("messageId") or payload.get("message_id") or "").strip()[:128]
    if not message_id:
        return None
    source_message = AgentRunMessage.objects.filter(run=event.run, source_message_id=message_id).first()
    if source_message is not None:
        return source_message
    role = ""
    parts: list[str] = []
    for candidate in event.run.events.filter(
        event_type__in=["TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END"],
        seq__lte=event.seq,
    ).order_by("seq"):
        candidate_payload = candidate.payload_json or {}
        candidate_id = str(candidate_payload.get("messageId") or candidate_payload.get("message_id") or "")
        if candidate_id != message_id:
            continue
        if candidate.event_type == "TEXT_MESSAGE_START":
            role = str(candidate_payload.get("role") or "")
        elif candidate.event_type == "TEXT_MESSAGE_CONTENT":
            parts.append(str(candidate_payload.get("delta") or ""))
    text = "".join(parts).strip()
    if role not in {AgentRunMessage.ROLE_USER, AgentRunMessage.ROLE_ASSISTANT} or not text:
        return None
    if role == AgentRunMessage.ROLE_USER:
        sequence = event.run.messages.order_by("-sequence").values_list("sequence", flat=True).first() or 0
        return AgentRunMessage.objects.create(
            run=event.run,
            sequence=sequence + 1,
            turn_index=_run_message_turn(event.run),
            role=AgentRunMessage.ROLE_USER,
            content=text,
            content_blocks=[{"type": "markdown", "text": text}],
            source_message_id=message_id,
        )
    latest = AgentRunMessage.objects.select_for_update().filter(run=event.run).order_by("-sequence").first()
    if latest is not None and latest.role == AgentRunMessage.ROLE_ASSISTANT:
        block = {"type": "markdown", "text": text}
        blocks = list(latest.content_blocks or [])
        if block not in blocks:
            blocks.append(block)
            latest.content = "\n\n".join(
                str(item.get("text") or "").strip()
                for item in blocks
                if item.get("type") == "markdown" and str(item.get("text") or "").strip()
            )
            latest.content_blocks = blocks
            latest.save(update_fields=["content", "content_blocks"])
        return latest
    sequence = event.run.messages.order_by("-sequence").values_list("sequence", flat=True).first() or 0
    return AgentRunMessage.objects.create(
        run=event.run,
        sequence=sequence + 1,
        turn_index=_run_message_turn(event.run),
        role=AgentRunMessage.ROLE_ASSISTANT,
        content=text,
        content_blocks=[{"type": "markdown", "text": text}],
        source_message_id=message_id,
    )


def _safe_run_result_blocks(value: Any, *, run=None) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    blocked = {"authorization", "cookie", "password", "secret", "token", "object_key", "internal_url"}

    def add(candidate: Any) -> None:
        if isinstance(candidate, str):
            text = candidate.strip()
            if not text:
                return
            if text[:1] in {"{", "["}:
                try:
                    add(json.loads(text))
                    return
                except (TypeError, ValueError):
                    pass
            if not any(item.get("type") == "markdown" and item.get("text") == text for item in blocks):
                blocks.append({"type": "markdown", "text": text[:20000]})
            return
        if not isinstance(candidate, dict):
            return
        if candidate.get("type") == "image":
            asset_id = str(candidate.get("asset_id") or "")
            try:
                asset = run.display_assets.filter(id=uuid.UUID(asset_id)).first() if run is not None and asset_id else None
            except (ValueError, exceptions.ValidationError):
                asset = None
            if asset is not None:
                block = {"type": "image", "url": f"/api/v1/agent-runs/{run.id}/display-assets/{asset.id}/", "title": str(candidate.get("title") or "Image")[:256], "alt": str(candidate.get("alt") or "")[:1024]}
                if block not in blocks:
                    blocks.append(block)
            return
        for key in ("message", "text", "output", "result"):
            if key in candidate:
                add(candidate.get(key))
        content = candidate.get("content")
        if isinstance(content, str):
            add(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and str(item.get("type") or "") in {"text", "markdown"}:
                    add(item.get("text"))
                elif isinstance(item, dict) and item.get("type") == "image":
                    add(item)
        structured = candidate.get("structuredContent")
        if isinstance(structured, dict):
            add(structured)
        fields: list[dict[str, str]] = []
        for key, item in candidate.items():
            normalized = str(key).lower()
            if normalized in blocked or normalized in {"content", "structuredcontent", "iserror"}:
                continue
            if normalized in {"message", "text", "output", "result"} and isinstance(item, str):
                continue
            if isinstance(item, bool):
                display = "Yes" if item else "No"
            elif isinstance(item, (int, float)):
                display = str(item)
            elif isinstance(item, str) and item.strip() and len(item) <= 500:
                display = item.strip()
            else:
                continue
            fields.append({"label": str(key).replace("_", " ").strip().title()[:80], "value": display[:500]})
            if len(fields) >= 12:
                break
        if fields and not any(item.get("type") == "fields" for item in blocks):
            blocks.append({"type": "fields", "items": fields})

    add(value)
    return blocks[:16]


@transaction.atomic
def materialize_private_run_result(
    *, run: AgentDisplayRun, value: Any, succeeded: bool = True
) -> AgentRunMessage | None:
    locked = AgentDisplayRun.objects.select_for_update().get(id=run.id)
    if not locked.messages.filter(role=AgentRunMessage.ROLE_USER).exists():
        return None
    blocks = _safe_run_result_blocks(value, run=locked)
    latest = locked.messages.select_for_update().order_by("-sequence").first()
    if not blocks and latest is not None and latest.role == AgentRunMessage.ROLE_ASSISTANT:
        return latest
    if not blocks and not succeeded:
        return None
    if not blocks:
        blocks = [{"type": "markdown", "text": "Agent completed the task."}]
    if latest is not None and latest.role == AgentRunMessage.ROLE_ASSISTANT:
        existing = latest
        merged = list(existing.content_blocks or [])
        for block in blocks:
            if block not in merged:
                merged.append(block)
        if merged != list(existing.content_blocks or []):
            markdown = [
                str(item.get("text") or "").strip()
                for item in merged
                if item.get("type") == "markdown" and str(item.get("text") or "").strip()
            ]
            existing.content = "\n\n".join(markdown) or existing.content
            existing.content_blocks = merged
            existing.save(update_fields=["content", "content_blocks"])
        return existing
    content = next(
        (str(item.get("text") or "").strip() for item in blocks if item.get("type") == "markdown" and str(item.get("text") or "").strip()),
        "Agent completed the task.",
    )
    sequence = locked.messages.order_by("-sequence").values_list("sequence", flat=True).first() or 0
    message = AgentRunMessage.objects.create(
        run=locked,
        sequence=sequence + 1,
        turn_index=_run_message_turn(locked),
        role=AgentRunMessage.ROLE_ASSISTANT,
        content=content,
        content_blocks=blocks,
    )
    if locked.display_title_source == AgentDisplayRun.DISPLAY_TITLE_PENDING:
        locked.display_title = _display_title(content)
        locked.display_title_source = AgentDisplayRun.DISPLAY_TITLE_DERIVED
        locked.save(update_fields=["display_title", "display_title_source", "updated_at"])
    return message


def _run_message_turn(run: AgentDisplayRun) -> int:
    try:
        task = run.execution_task
    except AgentExecutionTask.DoesNotExist:
        return 1
    return max(int((task.request_json or {}).get("turn_index") or 1), 1)


def sync_output_artifacts_from_run(*, run: AgentDisplayRun) -> None:
    for event in run.events.filter(event_type="CUSTOM").order_by("seq"):
        sync_output_artifact_from_event(event=event)


def sync_output_artifact_from_event(*, event: AgentDisplayEvent) -> AgentOutputArtifact | None:
    if event.run.run_kind == AgentDisplayRun.KIND_DEMO:
        return None
    payload = event.payload_json or {}
    if event.event_type == "CUSTOM" and payload.get("name") == "nexus.image.created":
        return sync_image_output_artifact(event=event)
    manifest = output_manifest_from_event(event=event)
    if manifest is None:
        return None
    from .computer_switching import event_computer_revision
    revision = event_computer_revision(event)
    turn_index = _run_message_turn(event.run)
    existing = event.run.output_artifacts.filter(
        workspace_path=manifest["workspace_path"],
        computer_revision=revision,
        turn_index=turn_index,
    ).first()
    if existing is not None and existing.producer_step.startswith("display_image:"):
        # A subsequent file event cannot remove the caller-bound image marker.
        return existing
    artifact, _ = AgentOutputArtifact.objects.update_or_create(
        run=event.run,
        turn_index=turn_index,
        workspace_path=manifest["workspace_path"],
        computer_revision=revision,
        defaults={
            "tenant": event.tenant,
            "project": event.agent.project,
            "agent": event.agent,
            "runtime": event.run.runtime,
            "source_event": event,
            "original_file_name": manifest["original_file_name"],
            "content_type": manifest["content_type"],
            "producer_step": manifest["producer_step"],
            "license_status": manifest["license_status"],
        },
    )
    return artifact


def sync_image_output_artifact(*, event: AgentDisplayEvent) -> AgentOutputArtifact | None:
    """Promote explicitly reported images, never ordinary browser frames."""
    from .runtime_services import snapshot_invocation_output_content
    from .computer_switching import event_computer_revision
    revision = event_computer_revision(event)
    turn_index = _run_message_turn(event.run)

    value = (event.payload_json or {}).get("value")
    if event.run.run_kind != AgentDisplayRun.KIND_INVOCATION or not isinstance(value, dict):
        return None
    try:
        asset_id = uuid.UUID(str(value.get("asset_id") or ""))
    except (TypeError, ValueError):
        return None
    asset = event.run.display_assets.filter(id=asset_id).first()
    extensions = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
    if asset is None or asset.content_type not in extensions or not 0 < asset.size_bytes <= 2 * 1024 * 1024:
        return None
    path = f"images/{asset.id}{extensions[asset.content_type]}"
    existing = event.run.output_artifacts.filter(
        workspace_path=path,
        computer_revision=revision,
        turn_index=turn_index,
    ).first()
    if existing is not None and existing.snapshot_status == "ready" and existing.producer_step == f"display_image:{asset.id}":
        return existing
    try:
        with asset.file.open("rb") as stream:
            content = stream.read(2 * 1024 * 1024 + 1)
    except OSError:
        return None
    if len(content) != asset.size_bytes or not secrets.compare_digest(hashlib.sha256(content).hexdigest(), asset.sha256):
        return None
    artifact = snapshot_invocation_output_content(
        run=event.run,
        path=path,
        content=content,
        content_type=asset.content_type,
        computer_revision=revision,
        turn_index=turn_index,
    )
    artifact.producer_step = f"display_image:{asset.id}"
    artifact.source_event = event
    artifact.save(update_fields=["producer_step", "source_event", "updated_at"])
    return artifact


def sync_memory_item_from_event(*, event: AgentDisplayEvent) -> AgentMemoryItem | None:
    if event.run.run_kind == AgentDisplayRun.KIND_DEMO:
        return None
    payload = event.payload_json or {}
    if not isinstance(payload, dict):
        return None
    if payload.get("type") != "CUSTOM" or payload.get("name") != AGUI_CUSTOM_MEMORY_ITEM:
        return None
    value = payload.get("value")
    if not isinstance(value, dict):
        return None
    content_text = str(value.get("content_text") or value.get("text") or "").strip()
    content_json = value.get("content_json") if isinstance(value.get("content_json"), dict) else {}
    if not content_text and not content_json:
        return None
    existing = event.agent.memory_items.filter(source_run=event.run, source_event_ids=[str(event.id)]).first()
    if existing is not None:
        return existing
    memory_type = str(value.get("memory_type") or AgentMemoryItem.TYPE_FACT)
    if memory_type not in {choice[0] for choice in AgentMemoryItem.TYPE_CHOICES}:
        memory_type = AgentMemoryItem.TYPE_FACT
    sensitivity_level = str(value.get("sensitivity_level") or AgentMemoryItem.SENSITIVITY_INTERNAL)
    if sensitivity_level not in {choice[0] for choice in AgentMemoryItem.SENSITIVITY_CHOICES}:
        sensitivity_level = AgentMemoryItem.SENSITIVITY_INTERNAL
    consent_status = str(value.get("consent_status") or AgentMemoryItem.CONSENT_PENDING)
    if consent_status not in {choice[0] for choice in AgentMemoryItem.CONSENT_CHOICES}:
        consent_status = AgentMemoryItem.CONSENT_PENDING
    license_status = str(value.get("license_status") or AgentMemoryItem.LICENSE_UNKNOWN)
    if license_status not in {choice[0] for choice in AgentMemoryItem.LICENSE_CHOICES}:
        license_status = AgentMemoryItem.LICENSE_UNKNOWN
    scope = str(value.get("scope") or AgentMemoryItem.SCOPE_CALLER)
    if scope not in {choice[0] for choice in AgentMemoryItem.SCOPE_CHOICES}:
        scope = AgentMemoryItem.SCOPE_CALLER
    try:
        confidence = Decimal(str(value.get("confidence", "1.0")))
    except Exception:
        confidence = Decimal("1.0")
    return AgentMemoryItem.objects.create(
        tenant=event.run.consumer_tenant or event.tenant,
        project=event.run.consumer_project or event.agent.project,
        agent=event.agent,
        actor=None,
        memory_type=memory_type,
        scope=scope,
        caller_subject_hash=event.run.caller_subject_hash if scope == AgentMemoryItem.SCOPE_CALLER else "",
        content_text=content_text,
        content_json=content_json,
        source_run=event.run,
        source_event_ids=[str(event.id)],
        confidence=confidence,
        sensitivity_level=sensitivity_level,
        consent_status=consent_status,
        license_status=license_status,
    )


def output_manifest_from_event(*, event: AgentDisplayEvent) -> dict[str, str] | None:
    payload = event.payload_json or {}
    if not isinstance(payload, dict):
        return None
    if payload.get("type") != "CUSTOM":
        return None
    if payload.get("name") not in {AGUI_CUSTOM_FILE_CREATED, AGUI_CUSTOM_FILE_UPDATED}:
        return None
    value = payload.get("value")
    if not isinstance(value, dict):
        return None
    workspace_path = str(
        value.get("workspace_path")
        or value.get("path")
        or value.get("file_path")
        or value.get("name")
        or value.get("file_name")
        or ""
    ).strip()
    if not workspace_path:
        return None
    normalized_path = workspace_path.replace("\\", "/").lstrip("/")
    if event.run.run_kind == AgentDisplayRun.KIND_INVOCATION and normalized_path.startswith("outputs/"):
        normalized_path = normalized_path[len("outputs/") :]
    original_name = str(value.get("original_file_name") or value.get("file_name") or value.get("name") or "").strip()
    if not original_name:
        original_name = PurePosixPath(normalized_path).name or "artifact.txt"
    content_type = str(value.get("content_type") or value.get("mime_type") or mimetypes.guess_type(original_name)[0] or "").strip()
    license_status = str(value.get("license_status") or AgentOutputArtifact.LICENSE_INTERNAL).strip()
    if license_status not in {AgentOutputArtifact.LICENSE_UNKNOWN, AgentOutputArtifact.LICENSE_INTERNAL, AgentOutputArtifact.LICENSE_APPROVED}:
        license_status = AgentOutputArtifact.LICENSE_UNKNOWN
    return {
        "workspace_path": normalized_path,
        "original_file_name": original_name[:255],
        "content_type": content_type[:128],
        "producer_step": str(value.get("producer_step") or value.get("step") or value.get("tool") or "").strip()[:255],
        "license_status": license_status,
    }


def is_text_output(content_type: str, file_name: str) -> bool:
    lower_name = file_name.lower()
    if content_type in TEXT_OUTPUT_CONTENT_TYPES or content_type.startswith("text/"):
        return True
    return lower_name.endswith((".csv", ".json", ".jsonl", ".log", ".md", ".markdown", ".txt"))


def ingest_display_event(*, run_id: str, token: str, event_type: str, payload: dict[str, Any], visibility: str = "public") -> AgentDisplayEvent:
    agui_event = normalize_agui_event(event_type=event_type, payload=payload)
    return ingest_agui_event(run_id=run_id, token=token, event=agui_event, visibility=visibility)


def ingest_agui_event(
    *,
    run_id: str,
    token: str,
    event: dict[str, Any],
    visibility: str = "public",
    client_event_id: str | None = None,
) -> AgentDisplayEvent:
    run = AgentDisplayRun.objects.select_related("agent", "tenant").filter(id=run_id).first()
    if run is None:
        raise AgentNotFound()
    if not token or not secrets.compare_digest(token, run.write_token):
        raise exceptions.PermissionDenied("Display run write token is invalid.")
    from .models import AgentTaskExecution
    execution = AgentTaskExecution.objects.select_related("task").filter(task__run=run).first()
    if execution and (execution.state != "running" or execution.cancel_requested_at
        or not execution.lease_expires_at or execution.lease_expires_at <= timezone.now()
        or execution.task.expires_at <= timezone.now()):
        raise exceptions.PermissionDenied("The Agent execution lease is not active.")
    agui_event = normalize_agui_event(event=event)
    return append_display_event(
        run=run,
        event_type=agui_event["type"],
        payload=agui_event,
        visibility=visibility,
        client_event_id=client_event_id,
    )


def submit_public_display_message(*, request, agent_id: str, run_id: str | None, content: str) -> list[AgentDisplayEvent]:
    return invoke_agent_policy("submit_public_display_message", request=request, agent_id=agent_id, run_id=run_id, content=content)


def public_demo_cookie_name(agent_id: str) -> str:
    return invoke_agent_policy("public_demo_cookie_name", agent_id=agent_id)


def get_public_display_run(*, agent_id: str, run_id: str | None = None, request=None) -> AgentDisplayRun:
    return invoke_agent_policy("get_public_display_run", agent_id=agent_id, run_id=run_id, request=request)


def get_public_display_asset(*, request, agent_id: str, run_id: str, asset_id: str) -> AgentDisplayAsset:
    return invoke_agent_policy("get_public_display_asset", request=request, agent_id=agent_id, run_id=run_id, asset_id=asset_id)


def list_public_display_events(*, run: AgentDisplayRun, cursor: int = 0, limit: int = 100) -> list[AgentDisplayEvent]:
    cursor = max(int(cursor or 0), 0)
    limit = min(max(int(limit or 100), 1), 500)
    return list(run.events.filter(visibility="public", seq__gt=cursor).order_by("seq")[:limit])


def list_private_display_events(*, run: AgentDisplayRun, cursor: int = 0, limit: int = 100) -> list[AgentDisplayEvent]:
    """Return caller-visible events after the Run identity check has passed."""

    cursor = max(int(cursor or 0), 0)
    limit = min(max(int(limit or 100), 1), 500)
    return list(
        run.events.filter(
            visibility__in=("public", "private"),
            seq__gt=cursor,
        ).order_by("seq")[:limit]
    )


def public_display_run(run: AgentDisplayRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "agent_id": str(run.agent_id),
        "status": run.status,
        "title": run.title,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "latest_seq": run.events.order_by("-seq").values_list("seq", flat=True).first() or 0,
    }


def public_display_event(event: AgentDisplayEvent) -> dict[str, Any]:
    agui_event = normalize_agui_event(event_type=event.event_type, payload=event.payload_json)
    return {
        "id": str(event.id),
        "agent_id": str(event.agent_id),
        "run_id": str(event.run_id),
        "seq": event.seq,
        **agui_event,
        "created_at": event.created_at,
    }


def public_display_events(*, run: AgentDisplayRun, cursor: int = 0, limit: int = 100) -> list[dict[str, Any]]:
    return [public_display_event(event) for event in list_public_display_events(run=run, cursor=cursor, limit=limit)]


def private_display_events(*, run: AgentDisplayRun, cursor: int = 0, limit: int = 100) -> list[dict[str, Any]]:
    return [public_display_event(event) for event in list_private_display_events(run=run, cursor=cursor, limit=limit)]


def normalize_agui_event(
    *,
    event_type: str | None = None,
    payload: dict[str, Any] | None = None,
    event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = dict(event or payload or {})
    raw_type = str(raw.get("type") or event_type or "").strip()
    normalized_type = normalize_agui_event_type(raw_type)
    if not normalized_type:
        raise exceptions.ValidationError({"type": ["This field is required."]})
    raw["type"] = normalized_type

    if normalized_type == "CUSTOM":
        raw = normalize_custom_event(raw)
    elif normalized_type in {"RUN_STARTED", "RUN_FINISHED", "RUN_ERROR"}:
        raw = normalize_run_event(raw)
    elif normalized_type in {"STEP_STARTED", "STEP_FINISHED"}:
        raw = normalize_step_event(raw)
    elif normalized_type in {"ACTIVITY_SNAPSHOT"}:
        raw = normalize_activity_snapshot(raw)
    elif normalized_type in {"ACTIVITY_DELTA", "STATE_DELTA"}:
        raw = normalize_patch_event(raw, patch_owner=normalized_type)
    elif normalized_type == "STATE_SNAPSHOT":
        raw = normalize_state_snapshot(raw)
    elif normalized_type == "MESSAGES_SNAPSHOT":
        raw = normalize_messages_snapshot(raw)
    elif normalized_type.startswith("TEXT_MESSAGE_"):
        raw = normalize_text_message_event(raw)
    elif normalized_type.startswith("TOOL_CALL_"):
        raw = normalize_tool_call_event(raw)
    elif "value" not in raw:
        value = {key: item for key, item in raw.items() if key not in {"type", "visibility"}}
        raw = {"type": normalized_type, "value": value}

    raw.pop("visibility", None)
    return raw


def normalize_run_event(raw: dict[str, Any]) -> dict[str, Any]:
    event_type = raw["type"]
    if event_type == "RUN_ERROR":
        value = raw.get("value") if isinstance(raw.get("value"), dict) else {}
        event = {
            "type": event_type,
            "message": str(raw.get("message") or value.get("message") or "Agent run failed."),
        }
        code = raw.get("code") or value.get("code")
        if code:
            event["code"] = str(code)
        return event
    value = raw.get("value") if isinstance(raw.get("value"), dict) else {}
    event = {
        "type": event_type,
        "threadId": str(raw.get("threadId") or value.get("threadId") or ""),
        "runId": str(raw.get("runId") or value.get("runId") or ""),
    }
    if raw.get("parentRunId") or value.get("parentRunId"):
        event["parentRunId"] = str(raw.get("parentRunId") or value.get("parentRunId"))
    if event_type == "RUN_FINISHED" and ("result" in raw or "result" in value):
        event["result"] = raw.get("result", value.get("result"))
    return event


def normalize_step_event(raw: dict[str, Any]) -> dict[str, Any]:
    value = raw.get("value") if isinstance(raw.get("value"), dict) else {}
    step_name = str(raw.get("stepName") or value.get("stepName") or raw.get("name") or "").strip()
    if not step_name:
        raise exceptions.ValidationError({"stepName": ["This field is required for STEP events."]})
    return {"type": raw["type"], "stepName": step_name}


def normalize_custom_event(raw: dict[str, Any]) -> dict[str, Any]:
    name = str(raw.get("name") or "").strip()
    value = raw.get("value")
    if value is None:
        value = {key: item for key, item in raw.items() if key not in {"type", "name", "visibility"}}
    if not name:
        raise exceptions.ValidationError({"name": ["This field is required for CUSTOM events."]})
    if name == AGUI_CUSTOM_COMPUTER_FRAME:
        value = normalize_computer_frame_value(value)
    return {"type": "CUSTOM", "name": name, "value": value}


def normalize_computer_frame_value(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise exceptions.ValidationError({"value": ["nexus.computer.frame value must be an object."]})
    frame = dict(value)
    screenshot_url = str(frame.get("screenshot_url") or frame.get("screenshotUrl") or frame.get("image_url") or "").strip()
    content_url = str(frame.get("content_url") or frame.get("contentUrl") or "").strip()
    if not screenshot_url and not content_url:
        raise exceptions.ValidationError({"value": ["nexus.computer.frame requires screenshot_url or content_url."]})
    if screenshot_url and not is_allowed_frame_image_reference(screenshot_url):
        raise exceptions.ValidationError({"screenshot_url": ["Expected an http(s) URL or data:image/* URL."]})
    if content_url and not content_url.startswith(("http://", "https://")):
        raise exceptions.ValidationError({"content_url": ["Expected an http(s) URL."]})
    if screenshot_url and len(screenshot_url) > AGUI_SCREENSHOT_URL_MAX_LENGTH:
        raise exceptions.ValidationError({"screenshot_url": [f"Ensure this field has no more than {AGUI_SCREENSHOT_URL_MAX_LENGTH} characters."]})

    # Browser DOM, input values and model observations stay inside Agent
    # Serving. Only the protected image and bounded display metadata cross the
    # Cloud boundary, even if a custom Agent attempts to add extra fields.
    safe_keys = {
        "url", "title", "text", "frame_id", "width", "height",
        "observation_id", "revision", "action", "action_status",
        "dom_node_count", "source", "computer_name", "profile",
    }
    normalized: dict[str, Any] = {
        key: item for key, item in frame.items() if key in safe_keys
    }
    if screenshot_url:
        normalized["screenshot_url"] = screenshot_url
    if content_url:
        normalized["content_url"] = content_url
    for key in ["url", "title", "text", "frame_id", "observation_id", "action", "action_status", "source", "computer_name", "profile"]:
        if key in normalized and normalized[key] is not None:
            normalized[key] = str(normalized[key])
    if normalized.get("source") not in {"attached_computer", "agent_host"}:
        normalized.pop("source", None)
    if normalized.get("profile") not in {"isolated", "local"}:
        normalized.pop("profile", None)
    if "computer_name" in normalized:
        normalized["computer_name"] = normalized["computer_name"][:128]
    if "url" in normalized:
        normalized["url"] = safe_browser_display_url(normalized["url"])
    for key in ["width", "height", "revision", "dom_node_count"]:
        if key in normalized and normalized[key] not in {None, ""}:
            try:
                minimum = 1 if key in {"width", "height"} else 0
                normalized[key] = max(int(normalized[key]), minimum)
            except (TypeError, ValueError) as exc:
                raise exceptions.ValidationError({key: ["Expected an integer."]}) from exc
    for key, limit in {
        "url": 2048,
        "title": 512,
        "text": 2048,
        "frame_id": 128,
        "observation_id": 128,
        "action": 64,
        "action_status": 32,
    }.items():
        if key in normalized:
            normalized[key] = normalized[key][:limit]
    return normalized


def safe_browser_display_url(value: Any) -> str:
    """Return display-safe HTTP(S) metadata without page credentials."""

    try:
        parsed = urlsplit(str(value or "").strip())
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return ""
        hostname = parsed.hostname
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        try:
            port = f":{parsed.port}" if parsed.port is not None else ""
        except ValueError:
            port = ""
        sensitive = ("token", "secret", "password", "passwd", "auth", "key", "session", "cookie")
        query = urlencode([
            (key, "[REDACTED]" if any(marker in key.casefold() for marker in sensitive) else item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        ])
        return urlunsplit((parsed.scheme.lower(), hostname + port, parsed.path, query, ""))
    except (TypeError, ValueError):
        return ""


def validate_frame_run_scope(value: dict[str, Any], *, run: AgentDisplayRun) -> None:
    """Asset-shaped URLs must identify this Run, including absolute aliases.

    The context-free normalizer only validates shape. Enforce ownership before
    persistence, so both SDK callbacks and internal event producers share it.
    Asset retrieval still performs its independent caller/token authorization.
    """
    for field in ("screenshot_url", "content_url"):
        reference = value.get(field)
        if not reference:
            continue
        try:
            path = unquote(urlsplit(reference).path)
            path = posixpath.normpath(path).rstrip("/") + "/"
        except ValueError as exc:
            raise exceptions.ValidationError({field: ["Invalid image reference."]}) from exc
        match = re.fullmatch(
            r"/api/v1/(?:public/agents/(?P<agent>[0-9a-f-]+)/display/runs/"
            r"(?P<public_run>[0-9a-f-]+)/assets/|agent-runs/(?P<private_run>[0-9a-f-]+)/display-assets/)"
            r"[0-9a-f-]+/", path, re.IGNORECASE,
        )
        if match and (
            (match.group("public_run") or match.group("private_run")).lower() != str(run.id)
            or (match.group("agent") and match.group("agent").lower() != str(run.agent_id))
        ):
            raise exceptions.ValidationError({field: ["The image reference must belong to this Run."]})


def is_allowed_frame_image_reference(value: str) -> bool:
    if value.startswith(("http://", "https://")):
        return True
    if value.startswith("data:image/") and ";base64," in value[:128]:
        return True
    # Run-scoped Display Assets deliberately use same-origin relative URLs so
    # browsers retain the caller/session authorization boundary.  Keep this
    # allowlist exact; arbitrary relative API URLs could otherwise turn an
    # Agent-authored image into a credentialed request to another endpoint.
    if re.fullmatch(
        r"/api/v1/(?:"
        r"public/agents/[0-9a-f-]+/display/runs/[0-9a-f-]+/assets/[0-9a-f-]+/|"
        r"agent-runs/[0-9a-f-]+/display-assets/[0-9a-f-]+/"
        r")",
        value,
        re.IGNORECASE,
    ):
        return True
    return False


def normalize_activity_snapshot(raw: dict[str, Any]) -> dict[str, Any]:
    content = raw.get("content")
    if content is None:
        content = raw.get("value")
    if content is None:
        content = {key: item for key, item in raw.items() if key not in {"type", "messageId", "activityType", "visibility"}}
    return {
        "type": "ACTIVITY_SNAPSHOT",
        "messageId": str(raw.get("messageId") or "task-plan"),
        "activityType": str(raw.get("activityType") or "PLAN"),
        "content": content if isinstance(content, dict) else {"value": content},
    }


def normalize_patch_event(raw: dict[str, Any], *, patch_owner: str) -> dict[str, Any]:
    patch = raw.get("patch")
    if patch is None:
        value = raw.get("value")
        patch = value.get("patch") if isinstance(value, dict) else value
    if patch is None:
        patch = []
    if not isinstance(patch, list):
        raise exceptions.ValidationError({"patch": ["Expected a JSON Patch array."]})
    event = {"type": patch_owner, "patch": patch}
    if patch_owner == "ACTIVITY_DELTA":
        event["messageId"] = str(raw.get("messageId") or "task-plan")
        event["activityType"] = str(raw.get("activityType") or "PLAN")
    return event


def normalize_state_snapshot(raw: dict[str, Any]) -> dict[str, Any]:
    snapshot = raw.get("snapshot")
    if snapshot is None:
        snapshot = raw.get("value")
    if snapshot is None:
        snapshot = {key: item for key, item in raw.items() if key not in {"type", "visibility"}}
    return {"type": "STATE_SNAPSHOT", "snapshot": snapshot if isinstance(snapshot, dict) else {"value": snapshot}}


def normalize_messages_snapshot(raw: dict[str, Any]) -> dict[str, Any]:
    messages = raw.get("messages")
    if messages is None:
        value = raw.get("value")
        messages = value.get("messages") if isinstance(value, dict) else value
    if messages is None:
        messages = []
    if not isinstance(messages, list):
        raise exceptions.ValidationError({"messages": ["Expected an array."]})
    return {"type": "MESSAGES_SNAPSHOT", "messages": messages}


def normalize_text_message_event(raw: dict[str, Any]) -> dict[str, Any]:
    event = {"type": raw["type"], "messageId": str(raw.get("messageId") or raw.get("id") or "")}
    if raw.get("role"):
        event["role"] = str(raw["role"])
    if raw["type"] in {"TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_CHUNK"}:
        event["delta"] = str(raw.get("delta") or raw.get("content") or raw.get("text") or raw.get("value") or "")
    return event


def normalize_tool_call_event(raw: dict[str, Any]) -> dict[str, Any]:
    value = raw.get("value") if isinstance(raw.get("value"), dict) else {}
    tool_call_id = raw.get("toolCallId") or raw.get("source_event_id") or value.get("source_event_id") or raw.get("id") or ""
    event = {"type": raw["type"], "toolCallId": str(tool_call_id)}
    tool_call_name = raw.get("toolCallName") or raw.get("name") or value.get("status")
    if tool_call_name:
        event["toolCallName"] = str(tool_call_name)
    if raw.get("parentMessageId"):
        event["parentMessageId"] = str(raw["parentMessageId"])
    if raw["type"] == "TOOL_CALL_ARGS":
        event["delta"] = str(raw.get("delta") or raw.get("args") or raw.get("value") or "")
    elif raw["type"] == "TOOL_CALL_RESULT":
        event["result"] = raw.get("result", raw.get("value"))
    else:
        extras = {
            key: item
            for key, item in raw.items()
            if key
            not in {
                "type",
                "toolCallId",
                "id",
                "toolCallName",
                "parentMessageId",
                "name",
                "visibility",
                "value",
            }
        }
        if raw.get("value") is not None:
            event["value"] = raw["value"]
        elif extras:
            event["value"] = extras
    return event


def normalize_agui_event_type(value: str) -> str:
    if not value:
        return ""
    if value in AGUI_TYPE_ALIASES:
        return AGUI_TYPE_ALIASES[value]
    candidate = value.strip().replace("-", "_").replace(".", "_").upper()
    if candidate.startswith("DISPLAY_"):
        raise exceptions.ValidationError({"type": ["Legacy display.* events are not accepted. Send AG-UI events."]})
    return candidate


def sanitize_display_payload(value: Any, *, key: str = "") -> Any:
    if isinstance(value, dict):
        blocked = {"tenant_id", "project_id", "container_id", "internal_mcp_url", "write_token", "authorization"}
        return {str(item_key): sanitize_display_payload(item, key=str(item_key)) for item_key, item in value.items() if str(item_key).lower() not in blocked}
    if isinstance(value, list):
        return [sanitize_display_payload(item, key=key) for item in value]
    if isinstance(value, str):
        max_length = AGUI_SCREENSHOT_URL_MAX_LENGTH if key in AGUI_LARGE_STRING_KEYS else AGUI_DEFAULT_STRING_MAX_LENGTH
        return value[:max_length]
    return value


def validate_agent_publication(*, agent: Agent) -> None:
    return invoke_agent_policy("validate_agent_publication", agent=agent)


def agent_has_runtime_target(agent: Agent) -> bool:
    if agent.current_image_id:
        return True
    return agent.runtime_deployments.filter(
        runtime_kind__in=[
            AgentRuntimeDeployment.RUNTIME_OPENWRT_IPV6,
            AgentRuntimeDeployment.RUNTIME_OPENWRT_RELAY,
        ],
        edge_registration__isnull=False,
    ).exclude(status=SoftDeleteModel.STATUS_DELETED).exists()


def agent_has_managed_openwrt_runtime(agent: Agent) -> bool:
    return agent.runtime_deployments.filter(
        runtime_kind__in=[
            AgentRuntimeDeployment.RUNTIME_OPENWRT_IPV6,
            AgentRuntimeDeployment.RUNTIME_OPENWRT_RELAY,
        ],
        edge_registration__binding_mode__in=[
            EdgeAgentRegistration.BINDING_MANAGED,
            EdgeAgentRegistration.BINDING_SUPPRESSED,
        ],
    ).exclude(status=SoftDeleteModel.STATUS_DELETED).exists()


def resolve_project_from_request(*, request, tenant: Tenant) -> Project | None:
    project_id = getattr(request, "project_id", "")
    if not project_id:
        return None
    return Project.objects.filter(tenant=tenant, id=project_id, status=SoftDeleteModel.STATUS_ACTIVE).first()


def resolve_team_from_request(*, request, tenant: Tenant) -> Team | None:
    team_id = getattr(request, "team_id", "")
    if not team_id:
        return None
    return Team.objects.filter(tenant=tenant, id=team_id, status=SoftDeleteModel.STATUS_ACTIVE).first()


def require_agent_admin(*, request, tenant: Tenant) -> None:
    if not has_nexus_permission(request.user, tenant, "admin"):
        raise exceptions.PermissionDenied("Tenant admin permission is required.")


def log_write(*, request, action: str, agent: Agent, metadata: dict[str, Any] | None = None) -> None:
    log_audit(
        request=request,
        action=action,
        actor=request.user,
        resource_type="agent",
        resource_id=agent.pk,
        metadata=metadata or {},
    )
