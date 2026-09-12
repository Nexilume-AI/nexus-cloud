"""Restrict monitoring aggregation to actual resources of this installation."""
from uuid import UUID
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from apps.metrics.usage_window import window_metrics as usage_window
from .services import installation_context


def window_metrics(tenant, *, start=None, end=None, seconds=300, project_id="", resource_type="", resource_id=""):
    _, own_tenant, own_project = installation_context()
    if str(tenant.pk) != own_tenant or str(project_id or "") not in ("", own_project):
        raise PermissionDenied("Monitoring context does not belong to this personal instance.")
    # Background callers may omit a Project, but may never widen this instance.
    project_id = own_project
    if resource_type not in ("", "system", "agent", "router"):
        raise ValidationError({"resource_type": "Select system, Agent or Router operational metrics."})
    if resource_type in ("agent", "router"):
        try:
            resource_id = UUID(str(resource_id))
        except (ValueError, TypeError, AttributeError):
            raise ValidationError({"resource_id": "Use a valid resource ID."}) from None
        from apps.agents.models import Agent
        from apps.routers.models import Router
        model = Agent if resource_type == "agent" else Router
        if not model.objects.filter(tenant_id=own_tenant, project_id=own_project, pk=resource_id).exists():
            raise NotFound("Monitoring resource not found.")
    elif resource_id:
        raise ValidationError({"resource_id": "System metrics do not accept a resource ID."})
    return usage_window(tenant, start=start, end=end, seconds=seconds, project_id=project_id,
        resource_type=resource_type, resource_id=resource_id)
