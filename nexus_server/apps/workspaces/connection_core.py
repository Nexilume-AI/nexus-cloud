"""Shared Computer connection primitives, independent of Tool Setup credentials."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from rest_framework import exceptions, status
from apps.common.authorization import has_nexus_permission
from apps.common.models import SoftDeleteModel
from apps.common.project_scope import scope_queryset_to_current_project
from apps.common.request_context import get_tenant_from_request
from apps.common.subjects import request_subject
from apps.tenancy.models import Tenant, Project
from .models import WorkspaceConnection
from .context_policy import connection_context_valid, scope_connections

class WorkspaceError(exceptions.APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Workspace request failed."
    default_code = "WORKSPACE_ERROR"


class WorkspaceNotFound(WorkspaceError):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "Workspace resource not found."
    default_code = "NOT_FOUND"


class WorkspaceConfigConflict(WorkspaceError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The remote tool configuration changed. Reload it and review the change again."
    default_code = "TOOL_CONFIG_CONFLICT"


@dataclass
class RunnerResult:
    ok: bool
    facts: dict[str, Any]
    checks: list[dict[str, Any]]
    error: str = ""


def list_workspace_connections(*, request):
    tenant = get_tenant_from_request(request)
    require_workspace_own(request=request, tenant=tenant, action="workspace.connection.read_own")
    subject = request_subject(request)
    _claim_legacy_workspace_connections(request=request, tenant=tenant, subject=subject)
    queryset = scope_queryset_to_current_project(
        WorkspaceConnection.objects.filter(tenant=tenant, owner_subject_hash=subject.subject_hash), request
    ).exclude(status=SoftDeleteModel.STATUS_DELETED).select_related("runtime_device", "runtime_enrollment").order_by("-created_at")
    return scope_connections(request=request, queryset=queryset)


def get_workspace_connection(*, request, connection_id: str) -> WorkspaceConnection:
    tenant = get_tenant_from_request(request)
    require_workspace_own(request=request, tenant=tenant, action="workspace.connection.read_own")
    subject = request_subject(request)
    _claim_legacy_workspace_connections(request=request, tenant=tenant, subject=subject, connection_id=connection_id)
    connection = (
        scope_queryset_to_current_project(
            WorkspaceConnection.objects.filter(
                tenant=tenant,
                id=connection_id,
                owner_subject_hash=subject.subject_hash,
            ),
            request,
        )
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .select_related("runtime_device", "runtime_enrollment")
        .first()
    )
    if connection is None or not connection_context_valid(connection):
        raise WorkspaceNotFound("Workspace connection not found.")
    return connection


def _claim_legacy_workspace_connections(*, request, tenant: Tenant, subject, connection_id: str | None = None) -> None:
    """Assign pre-private-ownership Computers to their original authenticated creator."""
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False) or getattr(user, "id", None) is None:
        return
    queryset = WorkspaceConnection.objects.filter(
        tenant=tenant,
        created_by_id=user.id,
        owner_subject_hash="",
    ).exclude(status=SoftDeleteModel.STATUS_DELETED)
    if connection_id:
        queryset = queryset.filter(id=connection_id)
    queryset.update(
        owner_subject_type=subject.principal_type,
        owner_subject_hash=subject.subject_hash,
    )


def resolve_project(*, tenant: Tenant, project_id) -> Project | None:
    if not project_id:
        return None
    project = Project.objects.filter(tenant=tenant, id=project_id, status=SoftDeleteModel.STATUS_ACTIVE).first()
    if project is None:
        raise WorkspaceNotFound("Project not found.")
    return project


def require_workspace_own(*, request, tenant: Tenant, action: str, connection: WorkspaceConnection | None = None) -> None:
    if not has_nexus_permission(
        request.user,
        tenant,
        action,
        resource_type="workspace_connection" if connection else None,
        resource_id=str(connection.id) if connection else None,
    ):
        raise exceptions.PermissionDenied("Own Computer permission is required.")
