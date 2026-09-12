"""Operational monitoring for the validated, fixed Personal instance owner."""
from rest_framework.exceptions import PermissionDenied
from apps.common.request_context import get_tenant_from_request


def context(request, action="metrics.read"):
    tenant = get_tenant_from_request(request)
    if action not in {"metrics.read", "metrics.manage", "audit.read"}:
        raise PermissionDenied("This monitoring action is not part of the personal instance.")
    return tenant, str(request.project_id)


def financial(request, tenant):
    # Validate the caller even for unsupported features. No personal wallet,
    # settlement, organization role or implicit administrator fallback exists.
    own_tenant, _ = context(request)
    if own_tenant.pk != tenant.pk:
        raise PermissionDenied("Monitoring context does not belong to this personal instance.")
    return False


def capabilities(request):
    _, project_id = context(request)
    return {"read": True, "manage": True, "financial": False,
        "platform_diagnostics": False, "reports": False, "audit": True,
        "scope": "project", "project_id": project_id}


def redact_financial(data):
    """Defense in depth for retained operational snapshots, not a data source."""
    hidden = {"wallet", "ledger", "amount", "cost", "total_cost", "pricing_rate", "notification_channels", "target", "email"}
    if isinstance(data, dict):
        return {key: redact_financial(value) for key, value in data.items() if key not in hidden}
    if isinstance(data, list):
        return [redact_financial(value) for value in data]
    return data
