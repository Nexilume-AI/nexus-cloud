"""Personal user authentication, separate from Enterprise IAM and API keys.

Device and Run delegates must retain their dedicated authentication. They are
not accepted as personal owner credentials by these authenticators.
"""
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework import exceptions
from rest_framework.authentication import SessionAuthentication, TokenAuthentication, get_authorization_header

from apps.accounts.models import AccountProfile
from apps.common.jwt import decode_jwt
from .identity import DatabasePersonalIdentityBackend


def validate_owner(request, user):
    # Session user/profile objects may have been cached earlier in a request.
    # Read current state so disable/context changes cannot be bypassed by them.
    current = get_user_model().objects.select_related("account_profile").filter(pk=user.pk, is_active=True).first()
    profile = getattr(current, "account_profile", None) if current is not None else None
    if current is None or profile is None or profile.status != "active":
        raise exceptions.AuthenticationFailed("Personal owner is unavailable.")
    tenant_id, project_id = DatabasePersonalIdentityBackend().password_context(request=request, user=current)
    for header, expected in (("HTTP_X_NEXUS_TENANT", tenant_id), ("HTTP_X_NEXUS_PROJECT", project_id)):
        if request.META.get(header, "") not in ("", expected):
            raise exceptions.PermissionDenied("The requested context does not belong to this personal instance.")
    request.tenant_id, request.project_id = tenant_id, project_id
    return current, tenant_id, project_id


class PersonalSessionAuthentication(SessionAuthentication):
    def authenticate(self, request):
        # Keep DRF's real session authentication and CSRF enforcement intact.
        result = super().authenticate(request)
        if result is None:
            return None
        user, _, _ = validate_owner(request, result[0])
        return user, result[1]


class PersonalBearerAuthentication(TokenAuthentication):
    keyword = "Bearer"

    def authenticate(self, request):
        auth = get_authorization_header(request).split()
        if not auth:
            api_token = request.META.get("HTTP_X_API_KEY")
            if api_token and api_token.startswith("np-router-") and api_token.isascii() and len(api_token) <= 128:
                from .router_credentials import authenticate
                return authenticate(request, api_token)
            if api_token:
                raise exceptions.AuthenticationFailed("Use personal owner credentials for this endpoint.")
            return None
        if request.META.get("HTTP_X_API_KEY"):
            raise exceptions.AuthenticationFailed("Provide one credential header, not both Authorization and X-API-Key.")
        if auth[0].lower() != b"bearer":
            return None
        if len(auth) != 2 or len(auth[1]) > 16384:
            raise exceptions.AuthenticationFailed("Invalid Authorization header.")
        try:
            token = auth[1].decode("ascii")
        except UnicodeDecodeError:
            raise exceptions.AuthenticationFailed("Invalid Authorization header.") from None
        if token.startswith(("sa-nexus-", "sk-nexus-")):
            raise exceptions.AuthenticationFailed("Use personal owner credentials for this endpoint.")
        if token.startswith("np-router-"):
            from .router_credentials import authenticate
            return authenticate(request, token)
        if token.startswith("np-agent-"):
            from .agent_credentials import authenticate
            return authenticate(request, token)
        if token.count(".") == 2:
            return self.authenticate_owner_jwt(request, token)
        user, credential = self.authenticate_credentials(token)
        user, _, _ = validate_owner(request, user)
        return user, credential

    def authenticate_owner_jwt(self, request, token):
        try:
            payload = decode_jwt(token)
            # The shared decoder preserves legacy Enterprise token semantics.
            # Personal requests require the complete locally issued access JWT.
            if (payload.get("typ") != "access"
                    or payload.get("iss") != settings.NEXUS_JWT_ISSUER
                    or payload.get("aud") != settings.NEXUS_JWT_AUDIENCE
                    or type(payload.get("exp")) is not int
                    or type(payload.get("iat")) is not int
                    or payload["exp"] <= payload["iat"]
                    or payload["iat"] > time.time() + settings.NEXUS_JWT_LEEWAY_SECONDS
                    or not isinstance(payload.get("sub"), str)
                    or payload.get("user_id") != payload["sub"]):
                raise ValueError("Invalid owner claims")
            user = get_user_model().objects.get(pk=payload["sub"], is_active=True)
        except (ValueError, TypeError, OverflowError, UnicodeError, get_user_model().DoesNotExist,
                exceptions.AuthenticationFailed):
            raise exceptions.AuthenticationFailed("Invalid personal access token.") from None
        user, tenant_id, project_id = validate_owner(request, user)
        if payload.get("tenant_id") != tenant_id or payload.get("project_id") != project_id:
            raise exceptions.AuthenticationFailed("Personal access token context mismatch.")
        return user, payload
