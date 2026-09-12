from __future__ import annotations

import base64
import json
import time
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.conf import settings
from rest_framework import exceptions


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _private_key() -> rsa.RSAPrivateKey:
    filename = str(getattr(settings, "NEXUS_EDGE_JWT_PRIVATE_KEY_FILE", "") or "")
    if not filename:
        raise exceptions.APIException("Edge JWT signing key is not configured.")
    try:
        key = serialization.load_pem_private_key(Path(filename).read_bytes(), password=None)
    except (OSError, ValueError, TypeError) as exc:
        raise exceptions.APIException("Edge JWT signing key could not be loaded.") from exc
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise exceptions.APIException("Edge JWT signing key must be an RSA key of at least 2048 bits.")
    return key


def issue_edge_access_token(*, deployment) -> str:
    registration = deployment.edge_registration
    if registration is None:
        raise exceptions.APIException("OpenWrt Agent registration is unavailable.")
    now = int(time.time())
    ttl = max(30, min(int(getattr(settings, "NEXUS_EDGE_JWT_TTL_SECONDS", 120)), 300))
    header = {
        "alg": "RS256",
        "kid": str(getattr(settings, "NEXUS_EDGE_JWT_KEY_ID", "edge-rs256-1")),
        "typ": "at+jwt",
    }
    payload = {
        "iss": str(getattr(settings, "NEXUS_EDGE_JWT_ISSUER", "https://nexus.local/edge")),
        "aud": f"urn:nexus:router:{registration.node.router_id}",
        "sub": "service://nexus-server",
        "tenant": str(deployment.tenant_id),
        "source_agent": "service://nexus-server",
        "target_agent": registration.origin,
        # agent-gw authenticates the routing hop before enforcing invoke.
        # Cloud-issued tokens therefore need both least-privilege scopes;
        # agent.register remains exclusive to terminal registration tokens.
        "scope": "agent.route agent.invoke",
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
    }
    header_segment = _b64(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_segment = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    signature = _private_key().sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header_segment}.{payload_segment}.{_b64(signature)}"


def edge_jwks() -> dict:
    public_numbers = _private_key().public_key().public_numbers()

    def integer_bytes(value: int) -> bytes:
        return value.to_bytes((value.bit_length() + 7) // 8, "big")

    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": str(getattr(settings, "NEXUS_EDGE_JWT_KEY_ID", "edge-rs256-1")),
                "n": _b64(integer_bytes(public_numbers.n)),
                "e": _b64(integer_bytes(public_numbers.e)),
            }
        ]
    }
