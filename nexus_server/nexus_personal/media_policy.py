"""Personal media belongs to the current, durable installation owner only."""
from types import SimpleNamespace

from django.db.models import Q
from rest_framework import exceptions

from apps.datasets.models import MediaAsset
from apps.tenancy.models import Tenant
from .authentication import validate_owner


class PersonalMediaPolicy:
    def _context(self, request):
        if (not getattr(request.user, "is_authenticated", False)
                or getattr(request.user, "is_api_key_principal", False)
                or getattr(request, "api_key", None) is not None
                or getattr(request, "service_account", None) is not None):
            raise exceptions.NotAuthenticated("Sign in as the personal instance owner.")
        return validate_owner(request, request.user)

    def media_request_tenant(self, *, request):
        _, tenant_id, _ = self._context(request)
        return Tenant.objects.get(pk=tenant_id, status="active")

    def media_creation_fields(self, *, request, purpose):
        self._context(request)
        if purpose not in dict(MediaAsset.PURPOSE_CHOICES):
            raise exceptions.ValidationError({"purpose": "Unsupported personal media purpose."})
        return {}

    def visible_media_assets(self, *, user, tenant):
        # Legacy callers pass only user/tenant. Never trust their in-memory
        # owner/profile state: validate against the installation again.
        user, tenant_id, project_id = self._context(SimpleNamespace(user=user, META={}))
        if str(tenant.pk) != tenant_id:
            raise exceptions.NotFound("Media asset not found.")
        return MediaAsset.objects.filter(tenant_id=tenant_id, owner=user).exclude(
            status="deleted").filter(Q(project_id=project_id) | Q(project__isnull=True))

    def get_mutable_media_asset(self, *, request, asset_id):
        from apps.datasets.media_services import get_media_asset
        # Lookup rechecks request context, current ownership and expiry.
        return get_media_asset(request=request, asset_id=asset_id)
