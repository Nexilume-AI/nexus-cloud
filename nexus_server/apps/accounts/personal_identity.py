"""Fixed-owner identity policy for an explicitly provisioned personal instance.

This is not standalone edition settings. The installer must provision and pin
the owner/context; no request, email claim, or first web visitor can claim it.
"""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from rest_framework import exceptions

from .identity import ExternalIdentityError


class PersonalIdentityBackend:
    def owner_context(self):
        owner_id = getattr(settings, "NEXUS_PERSONAL_OWNER_ID", "")
        tenant_id = getattr(settings, "NEXUS_PERSONAL_TENANT_ID", "")
        project_id = getattr(settings, "NEXUS_PERSONAL_PROJECT_ID", "")
        return owner_id, tenant_id, project_id

    def password_context(self, *, request, user):
        owner_id, tenant_id, project_id = self.owner_context()
        if not all(isinstance(value, str) for value in (owner_id, tenant_id, project_id)) or not owner_id.strip() or not tenant_id.strip():
            raise ImproperlyConfigured("Personal instance owner and context have not been provisioned.")
        if not getattr(user, "is_active", False) or str(getattr(user, "pk", "")) != owner_id:
            raise exceptions.AuthenticationFailed("This identity cannot sign in to this personal instance.")
        profile = getattr(user, "account_profile", None)
        if profile is not None and (getattr(profile, "status", "active") != "active"
                                    or profile.tenant_id != tenant_id or profile.project_id != project_id):
            # Shared legacy login can reuse a saved profile when headers are
            # empty. Never let a stale profile replace the pinned personal scope.
            raise ImproperlyConfigured("Personal owner profile does not match the provisioned context.")
        if any(getattr(request, attribute, "") not in ("", fixed) for attribute, fixed in (
            ("tenant_id", tenant_id), ("project_id", project_id),
        )):
            raise exceptions.PermissionDenied("The requested context does not belong to this personal instance.")
        return tenant_id, project_id

    def authenticate_external_user(self, **kwargs):
        # No public signup and no automatic linking by matching the owner's
        # email. An explicit, separately reviewed linking flow is required.
        raise ExternalIdentityError("PERSONAL_EXTERNAL_SIGNIN_DISABLED", "Use the configured owner's password to sign in.")

    def authenticate_machine_token(self, *, request, token):
        # Organization machine identities are not part of a personal instance.
        # Dedicated Agent/Router/Computer credentials retain their own checks.
        raise exceptions.AuthenticationFailed("Machine identity tokens are not supported by this personal instance.")

    def authenticate_api_key(self, *, request, token):
        # Personal workload credentials use their own scoped authenticators.
        # Never load the Enterprise key registry or treat its keys as owner login.
        raise exceptions.AuthenticationFailed("Legacy API keys are not supported by this personal instance.")
