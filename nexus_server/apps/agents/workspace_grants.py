from __future__ import annotations
from .device_contracts import (WORKSPACE_CAPABILITIES, WORKSPACE_CAPABILITY_SET, WORKSPACE_SETUP_TOOLS, normalize_workspace_capabilities)

from typing import Iterable

from django.utils import timezone
from rest_framework import exceptions

from apps.audit.services import log_audit
from apps.common.models import SoftDeleteModel
from apps.common.subjects import request_subject
from apps.common.request_context import get_tenant_from_request

from .models import Agent, AgentWorkspaceGrant






def _grant_queryset(*, request, agent: Agent):
    tenant = get_tenant_from_request(request)
    subject = request_subject(request)
    queryset = AgentWorkspaceGrant.objects.filter(
        tenant=tenant,
        agent=agent,
        caller_subject_hash=subject.subject_hash,
    )
    project_id = getattr(request, "project_id", None) or None
    return queryset.filter(project_id=project_id) if project_id else queryset.filter(project__isnull=True)


def get_workspace_grant(*, request, agent: Agent) -> AgentWorkspaceGrant | None:
    return _grant_queryset(request=request, agent=agent).first()


def set_workspace_grant(*, request, agent: Agent, scopes: Iterable[str]) -> AgentWorkspaceGrant:
    requested = normalize_workspace_capabilities(scopes)
    declared = set(normalize_workspace_capabilities(agent.workspace_capabilities))
    if not set(requested).issubset(declared):
        raise exceptions.ValidationError({"scopes": "Grant scopes must be declared by the Agent."})
    tenant = get_tenant_from_request(request)
    subject = request_subject(request)
    project_id = getattr(request, "project_id", None) or None
    grant = _grant_queryset(request=request, agent=agent).first()
    if grant is None:
        grant = AgentWorkspaceGrant(
            tenant=tenant,
            project_id=project_id,
            agent=agent,
            caller_subject_hash=subject.subject_hash,
            caller_principal_type=subject.principal_type,
        )
    grant.scopes = requested
    grant.status = SoftDeleteModel.STATUS_ACTIVE
    grant.deleted_at = None
    grant.granted_at = timezone.now()
    grant.revoked_at = None
    grant.save()
    log_audit(
        request=request,
        action="agents.workspace.grant",
        actor=request.user,
        resource_type="agent_workspace_grant",
        resource_id=grant.id,
        metadata={"agent_id": str(agent.id), "scopes": requested},
    )
    return grant


def revoke_workspace_grant(*, request, agent: Agent) -> AgentWorkspaceGrant | None:
    grant = get_workspace_grant(request=request, agent=agent)
    if grant is None:
        return None
    grant.scopes = []
    grant.status = SoftDeleteModel.STATUS_DELETED
    grant.deleted_at = timezone.now()
    grant.revoked_at = timezone.now()
    grant.save(update_fields=["scopes", "status", "deleted_at", "revoked_at", "updated_at"])
    log_audit(
        request=request,
        action="agents.workspace.revoke",
        actor=request.user,
        resource_type="agent_workspace_grant",
        resource_id=grant.id,
        metadata={"agent_id": str(agent.id)},
    )
    return grant


def effective_workspace_capabilities(*, request, agent: Agent) -> list[str]:
    declared = set(normalize_workspace_capabilities(agent.workspace_capabilities))
    grant = get_workspace_grant(request=request, agent=agent)
    granted = set(grant.scopes or []) if grant and grant.status == SoftDeleteModel.STATUS_ACTIVE else set()
    return [value for value in WORKSPACE_CAPABILITIES if value in declared and value in granted]


def effective_workspace_capabilities_for_run(run) -> list[str]:
    declared = set(normalize_workspace_capabilities(run.agent.workspace_capabilities))
    queryset = AgentWorkspaceGrant.objects.filter(
        tenant=run.consumer_tenant,
        agent=run.agent,
        caller_subject_hash=run.caller_subject_hash,
        status=SoftDeleteModel.STATUS_ACTIVE,
    )
    queryset = queryset.filter(project=run.consumer_project) if run.consumer_project_id else queryset.filter(project__isnull=True)
    grant = queryset.first()
    granted = set(grant.scopes or []) if grant else set()
    return [value for value in WORKSPACE_CAPABILITIES if value in declared and value in granted]


def is_workspace_setup_tool(tool_name: str) -> bool:
    return str(tool_name or "") in WORKSPACE_SETUP_TOOLS


def grant_agent_for_request(*, request, agent_id: str) -> Agent:
    from .services import _get_bindable_agent

    return _get_bindable_agent(request=request, agent_id=agent_id)
