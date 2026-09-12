from __future__ import annotations

from rest_framework.permissions import BasePermission


class HasTenantContext(BasePermission):
    message = "X-Nexus-Tenant header is required."

    def has_permission(self, request, view) -> bool:
        return bool(getattr(request, "tenant_id", ""))
