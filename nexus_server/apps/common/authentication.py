from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework import exceptions
from rest_framework.authentication import TokenAuthentication, get_authorization_header

from .jwt import decode_jwt


class NexusBearerAuthentication(TokenAuthentication):
    keyword = "Bearer"

    def authenticate(self, request):
        auth = get_authorization_header(request).split()
        if not auth:
            api_key = request.META.get("HTTP_X_API_KEY", "")
            if api_key:
                token = str(api_key)
                if token.startswith("sk-nexus-"):
                    return self._authenticate_api_key(request, token)
                raise exceptions.AuthenticationFailed("Invalid X-Api-Key header.")
        if not auth:
            return None
        if auth[0].lower() != self.keyword.lower().encode():
            return None
        if len(auth) == 1:
            raise exceptions.NotAuthenticated("Authentication credentials were not provided.")
        if len(auth) != 2:
            raise exceptions.AuthenticationFailed("Invalid Authorization header.")

        token = auth[1].decode("utf-8")
        if token.startswith("sa-nexus-"):
            return self._authenticate_service_account(request, token)
        if token.startswith("sk-nexus-"):
            return self._authenticate_api_key(request, token)
        if token.count(".") == 2:
            return self._authenticate_jwt(token)
        return self.authenticate_credentials(auth[1].decode())

    def _authenticate_jwt(self, token: str):
        payload = decode_jwt(token)
        user_id = payload.get("sub") or payload.get("user_id")
        if not user_id:
            raise exceptions.AuthenticationFailed("JWT subject is required.")

        user_model = get_user_model()
        try:
            user = user_model.objects.get(pk=user_id)
        except user_model.DoesNotExist as exc:
            raise exceptions.AuthenticationFailed("User not found.") from exc
        if not user.is_active:
            raise exceptions.AuthenticationFailed("User inactive or deleted.")
        return user, payload

    def _authenticate_api_key(self, request, token: str):
        from apps.accounts.identity import authenticate_api_key

        return authenticate_api_key(request=request, token=token)

    def _authenticate_service_account(self, request, token: str):
        from apps.accounts.identity import authenticate_machine_token

        return authenticate_machine_token(request=request, token=token)
