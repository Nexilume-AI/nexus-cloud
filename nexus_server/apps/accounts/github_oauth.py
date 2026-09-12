from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.utils import timezone

from .google_oauth import authenticate_external_user, safe_return_path
from .models import ExternalIdentity


GITHUB_SESSION_KEY = "nexus_github_oauth"
GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_URL = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
MAX_GITHUB_RESPONSE_BYTES = 1024 * 1024


class GitHubOAuthError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class GitHubOAuthClient:
    client_id: str
    client_secret: str
    redirect_uri: str


def load_github_oauth_client() -> GitHubOAuthClient:
    client_id = settings.NEXUS_GITHUB_OAUTH_CLIENT_ID
    client_secret = settings.NEXUS_GITHUB_OAUTH_CLIENT_SECRET
    if not client_id and not client_secret:
        path = Path(settings.NEXUS_GITHUB_OAUTH_CLIENT_SECRET_FILE).expanduser()
        try:
            if not path.is_file() or path.stat().st_size > 64 * 1024:
                raise ValueError("missing or oversized OAuth client file")
            payload = json.loads(path.read_text(encoding="utf-8"))
            values = payload.get("web", payload)
            client_id = str(values["client_id"]).strip()
            client_secret = str(values["client_secret"]).strip()
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise GitHubOAuthError(
                "GITHUB_OAUTH_UNAVAILABLE",
                "GitHub sign-in is not configured correctly. Other sign-in methods are still available.",
            ) from exc
    redirect_uri = str(settings.NEXUS_GITHUB_OAUTH_REDIRECT_URI).strip()
    if not client_id or not client_secret or not redirect_uri:
        raise GitHubOAuthError("GITHUB_OAUTH_UNAVAILABLE", "GitHub sign-in is not configured correctly.")
    if not redirect_uri.startswith("https://") and not settings.DEBUG:
        raise GitHubOAuthError("GITHUB_OAUTH_UNAVAILABLE", "The GitHub OAuth callback must use HTTPS.")
    return GitHubOAuthClient(client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri)


def github_oauth_available() -> bool:
    try:
        load_github_oauth_client()
        return True
    except GitHubOAuthError:
        return False


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def begin_github_oauth(*, request, next_path: str | None) -> str:
    client = load_github_oauth_client()
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    request.session[GITHUB_SESSION_KEY] = {
        "state": state,
        "verifier": verifier,
        "next": safe_return_path(request, next_path),
        "created_at": int(timezone.now().timestamp()),
    }
    request.session.modified = True
    params = {
        "client_id": client.client_id,
        "redirect_uri": client.redirect_uri,
        "scope": "read:user user:email",
        "state": state,
        "code_challenge": _pkce_challenge(verifier),
        "code_challenge_method": "S256",
        "allow_signup": "true",
    }
    return f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}"


def consume_github_oauth_session(*, request, state: str) -> dict:
    stored = request.session.pop(GITHUB_SESSION_KEY, None)
    request.session.modified = True
    if not isinstance(stored, dict) or not secrets.compare_digest(str(stored.get("state", "")), state):
        raise GitHubOAuthError("GITHUB_OAUTH_STATE_INVALID", "The sign-in request expired or was already used. Please try again.")
    age = int(timezone.now().timestamp()) - int(stored.get("created_at", 0))
    if age < 0 or age > settings.NEXUS_GITHUB_OAUTH_STATE_TTL_SECONDS:
        raise GitHubOAuthError("GITHUB_OAUTH_STATE_EXPIRED", "The sign-in request expired. Please try again.")
    verifier = str(stored.get("verifier", ""))
    if len(verifier) < 43:
        raise GitHubOAuthError("GITHUB_OAUTH_STATE_INVALID", "The sign-in request could not be verified. Please try again.")
    return stored


def _request_json(*, url: str, method: str = "GET", token: str = "", form: dict | None = None):
    body = urlencode(form).encode("utf-8") if form is not None else None
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Nexilume-OAuth",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        headers["Accept"] = "application/json"
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=15) as response:
            raw = response.read(MAX_GITHUB_RESPONSE_BYTES + 1)
        if len(raw) > MAX_GITHUB_RESPONSE_BYTES:
            raise ValueError("oversized GitHub response")
        return json.loads(raw.decode("utf-8"))
    except (HTTPError, URLError, OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise GitHubOAuthError("GITHUB_OAUTH_VERIFICATION_FAILED", "GitHub could not verify this sign-in. Please try again.") from exc


def exchange_github_identity(*, code: str, verifier: str) -> dict:
    client = load_github_oauth_client()
    token_payload = _request_json(
        url=GITHUB_TOKEN_URL,
        method="POST",
        form={
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "code": code,
            "redirect_uri": client.redirect_uri,
            "code_verifier": verifier,
        },
    )
    access_token = str(token_payload.get("access_token", "")).strip() if isinstance(token_payload, dict) else ""
    if not access_token:
        raise GitHubOAuthError("GITHUB_OAUTH_VERIFICATION_FAILED", "GitHub did not issue a usable sign-in credential.")
    profile = _request_json(url=f"{GITHUB_API_URL}/user", token=access_token)
    emails = _request_json(url=f"{GITHUB_API_URL}/user/emails?per_page=100", token=access_token)
    if not isinstance(profile, dict) or not isinstance(emails, list):
        raise GitHubOAuthError("GITHUB_OAUTH_VERIFICATION_FAILED", "GitHub returned an invalid identity response.")
    primary = next(
        (
            item
            for item in emails
            if isinstance(item, dict) and item.get("primary") is True and item.get("verified") is True and item.get("email")
        ),
        None,
    )
    if primary is None:
        raise GitHubOAuthError("GITHUB_EMAIL_NOT_VERIFIED", "GitHub did not provide a verified primary email address.")
    return {
        "subject": str(profile.get("id", "")),
        "email": str(primary["email"]),
        "display_name": str(profile.get("name") or profile.get("login") or ""),
    }


def authenticate_github_user(*, request, identity: dict):
    return authenticate_external_user(
        request=request,
        provider=ExternalIdentity.PROVIDER_GITHUB,
        subject=str(identity.get("subject", "")),
        email=str(identity.get("email", "")),
        display_name=str(identity.get("display_name", "")),
        signup_mode=settings.NEXUS_GITHUB_OAUTH_SIGNUP_MODE,
        allowed_domains=settings.NEXUS_GITHUB_OAUTH_ALLOWED_DOMAINS,
        error_prefix="GITHUB",
    )
