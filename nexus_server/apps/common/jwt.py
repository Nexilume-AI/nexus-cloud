from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any

from django.conf import settings
from django.utils import timezone
from rest_framework import exceptions


def encode_jwt(payload: dict[str, Any]) -> str:
    header = {"alg": settings.NEXUS_JWT_ALGORITHM, "typ": "JWT"}
    header_segment = _b64encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_segment = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signed_part = f"{header_segment}.{payload_segment}".encode("ascii")
    signature_segment = _b64encode(_sign(signed_part))
    return f"{header_segment}.{payload_segment}.{signature_segment}"


def issue_token_pair(*, user, tenant_id: str = "", project_id: str = "") -> dict[str, str]:
    now = int(time.time())
    subject = str(user.pk)
    base_payload = {
        "sub": subject,
        "user_id": subject,
        "email": getattr(user, "email", ""),
        "tenant_id": tenant_id,
        "project_id": project_id,
        "iss": settings.NEXUS_JWT_ISSUER,
        "aud": settings.NEXUS_JWT_AUDIENCE,
        "iat": now,
    }
    access_payload = {
        **base_payload,
        "typ": "access",
        "jti": uuid.uuid4().hex,
        "exp": int((timezone.now() + timezone.timedelta(minutes=30)).timestamp()),
    }
    refresh_payload = {
        **base_payload,
        "typ": "refresh",
        "jti": uuid.uuid4().hex,
        "exp": int((timezone.now() + timezone.timedelta(days=14)).timestamp()),
    }
    return {
        "access_token": encode_jwt(access_payload),
        "refresh_token": encode_jwt(refresh_payload),
    }


def decode_jwt(token: str) -> dict[str, Any]:
    try:
        header_segment, payload_segment, signature_segment = token.split(".")
    except ValueError as exc:
        raise exceptions.AuthenticationFailed("Invalid JWT format.") from exc

    signed_part = f"{header_segment}.{payload_segment}".encode("ascii")
    header = _json_b64decode(header_segment)
    payload = _json_b64decode(payload_segment)

    if header.get("alg") != settings.NEXUS_JWT_ALGORITHM:
        raise exceptions.AuthenticationFailed("Unsupported JWT algorithm.")
    if header.get("typ") not in (None, "JWT"):
        raise exceptions.AuthenticationFailed("Unsupported JWT type.")

    expected_signature = _sign(signed_part)
    actual_signature = _b64decode(signature_segment)
    if not hmac.compare_digest(expected_signature, actual_signature):
        raise exceptions.AuthenticationFailed("Invalid JWT signature.")

    now = int(time.time())
    leeway = settings.NEXUS_JWT_LEEWAY_SECONDS
    exp = payload.get("exp")
    if exp is not None and now > int(exp) + leeway:
        raise exceptions.AuthenticationFailed("JWT has expired.")
    nbf = payload.get("nbf")
    if nbf is not None and now + leeway < int(nbf):
        raise exceptions.AuthenticationFailed("JWT is not yet valid.")
    issuer = payload.get("iss")
    if issuer is not None and issuer != settings.NEXUS_JWT_ISSUER:
        raise exceptions.AuthenticationFailed("Invalid JWT issuer.")
    audience = payload.get("aud")
    if audience is not None and audience != settings.NEXUS_JWT_AUDIENCE:
        raise exceptions.AuthenticationFailed("Invalid JWT audience.")

    return payload


def _json_b64decode(value: str) -> dict[str, Any]:
    try:
        decoded = _b64decode(value)
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise exceptions.AuthenticationFailed("Invalid JWT payload.") from exc
    if not isinstance(payload, dict):
        raise exceptions.AuthenticationFailed("Invalid JWT payload.")
    return payload


def _b64decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except ValueError as exc:
        raise exceptions.AuthenticationFailed("Invalid JWT encoding.") from exc


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _sign(value: bytes) -> bytes:
    if settings.NEXUS_JWT_ALGORITHM != "HS256":
        raise exceptions.AuthenticationFailed("Unsupported JWT algorithm.")
    return hmac.new(settings.SECRET_KEY.encode("utf-8"), value, hashlib.sha256).digest()
