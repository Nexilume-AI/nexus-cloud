"""Fixed personal installation context, never caller-selected membership."""
from rest_framework import exceptions

from apps.tenancy.models import Tenant
from .authentication import validate_owner


class PersonalRequestContext:
    def request_tenant(self, request):
        if (not getattr(request.user, "is_authenticated", False)
                or getattr(request.user, "is_api_key_principal", False)
                or getattr(request, "api_key", None) is not None
                or getattr(request, "service_account", None) is not None):
            raise exceptions.NotAuthenticated("Sign in as the personal instance owner.")
        _, tenant_id, _ = validate_owner(request, request.user)
        return Tenant.objects.get(pk=tenant_id, status="active")
