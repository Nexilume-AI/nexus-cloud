from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .models import ExternalIdentity
from .identity import ExternalIdentityError as GoogleOAuthError, authenticate_external_user


GOOGLE_SCOPES = ("openid", "email", "profile")
GOOGLE_SESSION_KEY = "nexus_google_oauth"


@dataclass(frozen=True)
class GoogleOAuthClient:
    client_id: str
    client_secret: str
    auth_uri: str
    token_uri: str
    redirect_uri: str

    def as_client_config(self) -> dict:
        return {
            "web": {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "auth_uri": self.auth_uri,
                "token_uri": self.token_uri,
                "redirect_uris": [self.redirect_uri],
            }
        }


def load_google_oauth_client() -> GoogleOAuthClient:
    path = Path(settings.NEXUS_GOOGLE_OAUTH_CLIENT_SECRET_FILE).expanduser()
    try:
        if not path.is_file() or path.stat().st_size > 64 * 1024:
            raise ValueError("missing or oversized OAuth client file")
        payload = json.loads(path.read_text(encoding="utf-8"))
        web = payload["web"]
        client = GoogleOAuthClient(
            client_id=str(web["client_id"]).strip(),
            client_secret=str(web["client_secret"]).strip(),
            auth_uri=str(web.get("auth_uri", "https://accounts.google.com/o/oauth2/auth")).strip(),
            token_uri=str(web.get("token_uri", "https://oauth2.googleapis.com/token")).strip(),
            redirect_uri=str(settings.NEXUS_GOOGLE_OAUTH_REDIRECT_URI).strip(),
        )
        if not all((client.client_id, client.client_secret, client.redirect_uri)):
            raise ValueError("incomplete OAuth client file")
        if not client.auth_uri.startswith("https://") or not client.token_uri.startswith("https://"):
            raise ValueError("OAuth endpoints must use HTTPS")
        if not client.redirect_uri.startswith("https://") and not settings.DEBUG:
            raise ValueError("OAuth callback must use HTTPS")
        return client
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise GoogleOAuthError(
            "GOOGLE_OAUTH_UNAVAILABLE",
            "Google sign-in is not configured correctly. Password sign-in is still available.",
        ) from exc


def google_oauth_available() -> bool:
    try:
        load_google_oauth_client()
        return True
    except GoogleOAuthError:
        return False


def safe_return_path(request, candidate: str | None) -> str:
    value = (candidate or "/").strip()
    if not value.startswith("/") or value.startswith("//"):
        return "/"
    if not url_has_allowed_host_and_scheme(
        value,
        allowed_hosts={request.get_host()},
        require_https=not settings.DEBUG,
    ):
        return "/"
    return value


def begin_google_oauth(*, request, next_path: str | None) -> str:
    client = load_google_oauth_client()
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    request.session[GOOGLE_SESSION_KEY] = {
        "state": state,
        "nonce": nonce,
        "next": safe_return_path(request, next_path),
        "created_at": int(timezone.now().timestamp()),
    }
    request.session.modified = True
    params = {
        "client_id": client.client_id,
        "redirect_uri": client.redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "state": state,
        "nonce": nonce,
        "include_granted_scopes": "true",
    }
    return f"{client.auth_uri}?{urlencode(params)}"


def consume_google_oauth_session(*, request, state: str) -> dict:
    stored = request.session.pop(GOOGLE_SESSION_KEY, None)
    request.session.modified = True
    if not isinstance(stored, dict) or not secrets.compare_digest(str(stored.get("state", "")), state):
        raise GoogleOAuthError("GOOGLE_OAUTH_STATE_INVALID", "The sign-in request expired or was already used. Please try again.")
    age = int(timezone.now().timestamp()) - int(stored.get("created_at", 0))
    if age < 0 or age > settings.NEXUS_GOOGLE_OAUTH_STATE_TTL_SECONDS:
        raise GoogleOAuthError("GOOGLE_OAUTH_STATE_EXPIRED", "The sign-in request expired. Please try again.")
    return stored


def exchange_google_identity(*, code: str, state: str, nonce: str) -> dict:
    client = load_google_oauth_client()
    try:
        from google.auth.transport.requests import Request as GoogleRequest
        from google.oauth2.id_token import verify_oauth2_token
        from google_auth_oauthlib.flow import Flow

        flow = Flow.from_client_config(client.as_client_config(), scopes=list(GOOGLE_SCOPES), state=state)
        flow.redirect_uri = client.redirect_uri
        flow.fetch_token(code=code)
        claims = verify_oauth2_token(flow.credentials.id_token, GoogleRequest(), client.client_id)
    except Exception as exc:
        raise GoogleOAuthError("GOOGLE_OAUTH_VERIFICATION_FAILED", "Google could not verify this sign-in. Please try again.") from exc
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise GoogleOAuthError("GOOGLE_OAUTH_NONCE_INVALID", "The sign-in response could not be verified. Please try again.")
    return claims


def authenticate_google_user(*, request, claims: dict):
    if claims.get("email_verified") is not True:
        raise GoogleOAuthError("GOOGLE_EMAIL_NOT_VERIFIED", "Google did not provide a verified email address.")
    return authenticate_external_user(
        request=request,
        provider=ExternalIdentity.PROVIDER_GOOGLE,
        subject=str(claims.get("sub", "")),
        email=str(claims.get("email", "")),
        display_name=str(claims.get("name", "")),
        signup_mode=settings.NEXUS_GOOGLE_OAUTH_SIGNUP_MODE,
        allowed_domains=settings.NEXUS_GOOGLE_OAUTH_ALLOWED_DOMAINS,
        error_prefix="GOOGLE",
    )
