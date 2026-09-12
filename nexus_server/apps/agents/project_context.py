"""Caller-private consent and immutable Project context snapshots for Agent Runs."""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions

from apps.common.models import SoftDeleteModel
from apps.common.subjects import request_subject
from apps.tenancy.models import Project
from apps.common.request_context import get_tenant_from_request

from .models import Agent, AgentProjectContextGrant


class AgentProjectContextPermissionRequired(exceptions.APIException):
    status_code = 409
    default_code = "PROJECT_CONTEXT_PERMISSION_REQUIRED"
    default_detail = "Allow this external Agent to read the selected Project instructions."


def selected_project(*, request, tenant) -> Project | None:
    project_id = getattr(request, "project_id", None)
    if not project_id:
        return None
    project = Project.objects.filter(
        id=project_id,
        tenant=tenant,
        status=SoftDeleteModel.STATUS_ACTIVE,
    ).first()
    if project is None:
        raise exceptions.NotFound()
    return project


def _grant(*, request, tenant, project: Project, agent: Agent):
    subject = request_subject(request)
    return AgentProjectContextGrant.objects.filter(
        tenant=tenant,
        project=project,
        agent=agent,
        caller_subject_hash=subject.subject_hash,
        status=SoftDeleteModel.STATUS_ACTIVE,
    ).first()


def project_context_state(*, request, agent: Agent) -> dict:
    tenant = get_tenant_from_request(request)
    project = selected_project(request=request, tenant=tenant)
    if project is None:
        return {
            "available": False,
            "authorized": True,
            "requires_authorization": False,
            "external_agent": agent.tenant_id != tenant.id,
            "project_id": "",
            "project_name": "All projects",
            "instructions_markdown": "",
            "revision": 0,
            "updated_at": None,
        }
    external = agent.tenant_id != tenant.id
    configured = bool(project.instructions_markdown)
    authorized = not external or not configured or _grant(
        request=request, tenant=tenant, project=project, agent=agent
    ) is not None
    return {
        "available": configured,
        "authorized": authorized,
        "requires_authorization": bool(external and configured and not authorized),
        "external_agent": external,
        "project_id": str(project.id),
        "project_name": project.name,
        "instructions_markdown": project.instructions_markdown,
        "revision": project.instructions_revision,
        "updated_at": project.instructions_updated_at.isoformat() if project.instructions_updated_at else None,
    }


def project_context_snapshot(*, request, agent: Agent) -> dict:
    state = project_context_state(request=request, agent=agent)
    if state["requires_authorization"]:
        raise AgentProjectContextPermissionRequired()
    if not state["available"]:
        return {}
    return {
        "project_id": state["project_id"],
        "project_name": state["project_name"],
        "instructions_markdown": state["instructions_markdown"],
        "instructions_revision": state["revision"],
        "captured_at": timezone.now().isoformat(),
    }


def ensure_run_project_context_authorized(*, request, run) -> None:
    snapshot = dict(run.project_context_snapshot or {})
    if not snapshot or run.agent.tenant_id == run.consumer_tenant_id:
        return
    subject = request_subject(request)
    allowed = AgentProjectContextGrant.objects.filter(
        tenant_id=run.consumer_tenant_id,
        project_id=snapshot.get("project_id"),
        agent=run.agent,
        caller_subject_hash=subject.subject_hash,
        status=SoftDeleteModel.STATUS_ACTIVE,
    ).exists()
    if not allowed:
        raise AgentProjectContextPermissionRequired()


@transaction.atomic
def authorize_project_context(*, request, agent: Agent) -> dict:
    tenant = get_tenant_from_request(request)
    project = selected_project(request=request, tenant=tenant)
    if project is None:
        raise exceptions.ValidationError({"project": "Select a Project before sharing its instructions."})
    if agent.tenant_id == tenant.id or not project.instructions_markdown:
        return project_context_state(request=request, agent=agent)
    subject = request_subject(request)
    existing = AgentProjectContextGrant.objects.select_for_update().filter(
        tenant=tenant,
        project=project,
        agent=agent,
        caller_subject_hash=subject.subject_hash,
    ).order_by("-created_at").first()
    if existing is None:
        AgentProjectContextGrant.objects.create(
            tenant=tenant,
            project=project,
            agent=agent,
            caller_principal_type=subject.principal_type,
            caller_subject_hash=subject.subject_hash,
        )
    elif existing.status != SoftDeleteModel.STATUS_ACTIVE:
        existing.status = SoftDeleteModel.STATUS_ACTIVE
        existing.deleted_at = None
        existing.revoked_at = None
        existing.granted_at = timezone.now()
        existing.caller_principal_type = subject.principal_type
        existing.save(update_fields=["status", "deleted_at", "revoked_at", "granted_at", "caller_principal_type", "updated_at"])
    return project_context_state(request=request, agent=agent)


@transaction.atomic
def revoke_project_context(*, request, agent: Agent) -> None:
    tenant = get_tenant_from_request(request)
    project = selected_project(request=request, tenant=tenant)
    if project is None:
        raise exceptions.NotFound()
    grant = _grant(request=request, tenant=tenant, project=project, agent=agent)
    if grant is None:
        return
    grant.status = SoftDeleteModel.STATUS_DELETED
    grant.deleted_at = timezone.now()
    grant.revoked_at = timezone.now()
    grant.save(update_fields=["status", "deleted_at", "revoked_at", "updated_at"])
