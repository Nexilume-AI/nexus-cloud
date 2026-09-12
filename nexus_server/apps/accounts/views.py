from __future__ import annotations

from urllib.parse import urlencode

from django.http import HttpResponseRedirect
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.openapi import TENANT_HEADERS, success_response

from .serializers import (
    AccountProfileSerializer,
    AccountUpdateSerializer,
    ChangePasswordResultSerializer,
    ChangePasswordSerializer,
    LoginSerializer,
    LoginResponseSerializer,
    PasswordResetSerializer,
    WhoAmISerializer,
)
from .services import (
    change_password,
    ensure_profile,
    get_profile_for_user,
    login_user,
    logout_user,
    request_password_reset,
    update_profile,
)
from .google_oauth import (
    GoogleOAuthError,
    authenticate_google_user,
    begin_google_oauth,
    consume_google_oauth_session,
    exchange_google_identity,
    safe_return_path,
)
from .github_oauth import (
    GitHubOAuthError,
    authenticate_github_user,
    begin_github_oauth,
    consume_github_oauth_session,
    exchange_github_identity,
)


def _oauth_error_redirect(code: str, next_path: str = "/") -> HttpResponseRedirect:
    query = urlencode({"oauth_error": code, "next": next_path})
    response = HttpResponseRedirect(f"/login?{query}")
    response["Cache-Control"] = "no-store"
    return response


class GoogleOAuthStartView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(tags=["auth"], auth=[], responses=None)
    def get(self, request):
        try:
            authorization_url = begin_google_oauth(request=request, next_path=request.query_params.get("next"))
        except GoogleOAuthError as exc:
            return _oauth_error_redirect(exc.code)
        response = HttpResponseRedirect(authorization_url)
        response["Cache-Control"] = "no-store"
        return response


class GoogleOAuthCallbackView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(tags=["auth"], auth=[], responses=None)
    def get(self, request):
        state = str(request.query_params.get("state", ""))
        next_path = "/"
        try:
            oauth_session = consume_google_oauth_session(request=request, state=state)
            next_path = safe_return_path(request, oauth_session.get("next"))
            if request.query_params.get("error"):
                raise GoogleOAuthError("GOOGLE_OAUTH_CANCELLED", "Google sign-in was cancelled.")
            code = str(request.query_params.get("code", ""))
            if not code:
                raise GoogleOAuthError("GOOGLE_OAUTH_CODE_MISSING", "Google did not return an authorization code.")
            claims = exchange_google_identity(
                code=code,
                state=state,
                nonce=str(oauth_session.get("nonce", "")),
            )
            authenticate_google_user(request=request, claims=claims)
        except GoogleOAuthError as exc:
            return _oauth_error_redirect(exc.code, next_path)
        response = HttpResponseRedirect(next_path)
        response["Cache-Control"] = "no-store"
        return response


class GitHubOAuthStartView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(tags=["auth"], auth=[], responses=None)
    def get(self, request):
        try:
            authorization_url = begin_github_oauth(request=request, next_path=request.query_params.get("next"))
        except GitHubOAuthError as exc:
            return _oauth_error_redirect(exc.code)
        response = HttpResponseRedirect(authorization_url)
        response["Cache-Control"] = "no-store"
        return response


class GitHubOAuthCallbackView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(tags=["auth"], auth=[], responses=None)
    def get(self, request):
        state = str(request.query_params.get("state", ""))
        next_path = "/"
        try:
            oauth_session = consume_github_oauth_session(request=request, state=state)
            next_path = safe_return_path(request, oauth_session.get("next"))
            if request.query_params.get("error"):
                raise GitHubOAuthError("GITHUB_OAUTH_CANCELLED", "GitHub sign-in was cancelled.")
            code = str(request.query_params.get("code", ""))
            if not code:
                raise GitHubOAuthError("GITHUB_OAUTH_CODE_MISSING", "GitHub did not return an authorization code.")
            identity = exchange_github_identity(code=code, verifier=str(oauth_session.get("verifier", "")))
            authenticate_github_user(request=request, identity=identity)
        except (GitHubOAuthError, GoogleOAuthError) as exc:
            return _oauth_error_redirect(exc.code, next_path)
        response = HttpResponseRedirect(next_path)
        response["Cache-Control"] = "no-store"
        return response


class LoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["auth"],
        request=LoginSerializer,
        responses=success_response("LoginEnvelope", LoginResponseSerializer),
        auth=[],
    )
    def post(self, request):
        # Browser login requests carry Origin and must prove same-origin intent.
        # Non-browser API clients remain compatible with the token response.
        if request.META.get("HTTP_ORIGIN"):
            SessionAuthentication().enforce_csrf(request)
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        login_result = login_user(request=request, **serializer.validated_data)
        if request.headers.get("X-Nexus-Client") == "web":
            return Response(
                {
                    "tenant_id": login_result["tenant_id"],
                    "session_authenticated": True,
                }
            )
        return Response(login_result)


class LogoutView(APIView):
    @extend_schema(tags=["auth"], parameters=TENANT_HEADERS, responses=success_response("LogoutEnvelope", None))
    def post(self, request):
        return Response(logout_user(request=request))


class WhoAmIView(APIView):
    @extend_schema(tags=["account"], parameters=TENANT_HEADERS, responses=success_response("WhoAmIEnvelope", WhoAmISerializer))
    def get(self, request):
        user = request.user
        profile = getattr(user, "account_profile", None)
        display_name = getattr(profile, "display_name", "") if profile else ""
        current_tenant = getattr(request, "tenant_id", "") or getattr(profile, "tenant_id", "")
        return Response(
            {
                "user_id": str(user.pk),
                "email": getattr(user, "email", "") or "",
                "display_name": display_name,
                "is_superuser": bool(getattr(user, "is_superuser", False)),
                "current_tenant": current_tenant,
                "tenant_id": current_tenant,
                "roles": [],
            }
        )


class AccountMeView(APIView):
    @extend_schema(tags=["account"], parameters=TENANT_HEADERS, responses=success_response("AccountProfileEnvelope", AccountProfileSerializer))
    def get(self, request):
        profile = get_profile_for_user(user=request.user)
        return Response(AccountProfileSerializer(profile).data)

    @extend_schema(
        tags=["account"],
        parameters=TENANT_HEADERS,
        request=AccountUpdateSerializer,
        responses=success_response("AccountProfileUpdateEnvelope", AccountProfileSerializer),
    )
    def patch(self, request):
        serializer = AccountUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile = ensure_profile(user=request.user)
        profile = update_profile(request=request, profile=profile, data=serializer.validated_data)
        return Response(AccountProfileSerializer(profile).data)


class ChangePasswordView(APIView):
    @extend_schema(
        tags=["account"],
        parameters=TENANT_HEADERS,
        request=ChangePasswordSerializer,
        responses=success_response("ChangePasswordEnvelope", ChangePasswordResultSerializer),
    )
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        return Response(
            change_password(
                request=request,
                new_password=serializer.validated_data["new_password"],
            )
        )


class PasswordResetView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["auth"],
        request=PasswordResetSerializer,
        responses=success_response("PasswordResetEnvelope", None, status_code=status.HTTP_201_CREATED),
        auth=[],
    )
    def post(self, request):
        serializer = PasswordResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            request_password_reset(request=request, **serializer.validated_data),
            status=status.HTTP_201_CREATED,
        )
