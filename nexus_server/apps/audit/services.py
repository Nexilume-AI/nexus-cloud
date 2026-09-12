from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.http import HttpRequest
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import exceptions, status

from .models import AuditLog
from .action_names import configured_action_aliases


ACTION_ALIASES = configured_action_aliases()

REVERSE_ACTION_ALIASES: dict[str, list[str]] = {}
for legacy, canonical in ACTION_ALIASES.items():
    REVERSE_ACTION_ALIASES.setdefault(canonical, []).append(legacy)


class AuditLogNotFound(exceptions.APIException):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "Audit log not found."
    default_code = "NOT_FOUND"


def write_audit_log(
    *,
    actor,
    tenant,
    action: str,
    resource_type: str,
    resource_id,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    request: HttpRequest | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    request_tenant_id = getattr(request, "tenant_id", "") if request else ""
    project_id = getattr(request, "project_id", "") if request else ""
    tenant_id = str(getattr(tenant, "id", tenant or request_tenant_id or ""))
    audit_metadata = metadata or {}
    service_account = getattr(request, "service_account", None) if request else None
    api_key = getattr(request, "api_key", None) if request else None
    if service_account is not None:
        audit_metadata = {
            **audit_metadata,
            "actor_principal_type": "service_account",
            "actor_principal_id": str(service_account.id),
        }
    if api_key is not None:
        audit_metadata = {
            **audit_metadata,
            "actor_principal_type": "api_key",
            "actor_principal_id": str(api_key.id),
        }
    return AuditLog.objects.create(
        tenant_id=tenant_id,
        project_id=project_id,
        actor=audit_actor(actor),
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id else "",
        before_snapshot=before or {},
        after_snapshot=after or {},
        request_id=getattr(request, "request_id", "") if request else "",
        ip_address=_client_ip(request) if request else None,
        user_agent=(request.META.get("HTTP_USER_AGENT", "")[:512] if request else ""),
        metadata=audit_metadata,
    )


def log_audit(
    *,
    request: HttpRequest | None,
    action: str,
    actor=None,
    resource_type: str = "",
    resource_id: str = "",
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    return write_audit_log(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        tenant=getattr(request, "tenant_id", "") if request else "",
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        before=before,
        after=after,
        request=request,
        metadata=metadata or {},
    )


def list_audit_logs(*, request):
    from apps.tenancy.services import get_tenant_from_request

    tenant = get_tenant_from_request(request)
    require_audit_permission(user=request.user, tenant=tenant)
    queryset = AuditLog.objects.filter(tenant_id=str(tenant.id)).select_related("actor").order_by("-created_at")
    action = request.query_params.get("action", "")
    if action:
        queryset = queryset.filter(action__in=action_filter_values(action))
    resource_type = request.query_params.get("resource_type", "")
    if resource_type:
        queryset = queryset.filter(resource_type=resource_type)
    resource_id = request.query_params.get("resource_id", "")
    if resource_id:
        queryset = queryset.filter(resource_id=resource_id)
    actor = request.query_params.get("actor") or request.query_params.get("actor_id") or ""
    if actor:
        queryset = queryset.filter(actor_id=actor)
    created_from = parse_time_param(request.query_params.get("created_from") or request.query_params.get("start"))
    if created_from:
        queryset = queryset.filter(created_at__gte=created_from)
    created_to = parse_time_param(request.query_params.get("created_to") or request.query_params.get("end"))
    if created_to:
        queryset = queryset.filter(created_at__lte=created_to)
    return queryset


def get_audit_log(*, request, log_id) -> AuditLog:
    from apps.tenancy.services import get_tenant_from_request

    tenant = get_tenant_from_request(request)
    require_audit_permission(user=request.user, tenant=tenant)
    log = AuditLog.objects.filter(tenant_id=str(tenant.id), id=log_id).select_related("actor").first()
    if log is None:
        raise AuditLogNotFound()
    return log


def canonical_action(action: str) -> str:
    return ACTION_ALIASES.get(action, action)


def action_filter_values(action: str) -> list[str]:
    values = {action}
    values.update(REVERSE_ACTION_ALIASES.get(action, []))
    canonical = canonical_action(action)
    values.add(canonical)
    values.update(REVERSE_ACTION_ALIASES.get(canonical, []))
    return list(values)


def parse_time_param(value: str | None):
    if not value:
        return None
    return parse_datetime(value) or parse_date(value)


def require_audit_permission(*, user, tenant) -> None:
    from apps.common.authorization import has_nexus_permission

    if not has_nexus_permission(user, tenant, "audit"):
        raise exceptions.PermissionDenied("Audit permission is required.")


def _client_ip(request: HttpRequest) -> str | None:
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.META.get("REMOTE_ADDR")


def audit_actor(actor):
    user_model = get_user_model()
    if isinstance(actor, user_model) and getattr(actor, "is_authenticated", False):
        return actor
    return None
