from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class RequestSubject:
    principal_type: str
    principal_id: str
    subject_hash: str
    external: bool = False

    @property
    def masked(self) -> str:
        return f"{self.principal_type}:{self.subject_hash[:10]}"


def request_subject(request) -> RequestSubject:
    """Return a stable, non-secret identity for caller-owned resources."""

    from .execution_request import ExecutionRequest
    if isinstance(request, ExecutionRequest):
        return request.execution_subject

    api_key = getattr(request, "api_key", None)
    external_id = str(request.headers.get("X-Nexus-End-User") or "").strip()
    if api_key is not None:
        principal_type = "api_key_external" if external_id else "api_key"
        principal_id = str(api_key.id)
        subject_value = external_id or principal_id
        namespace = f"{api_key.tenant_id}|api_key|{principal_id}|{subject_value}"
        return RequestSubject(
            principal_type=principal_type,
            principal_id=principal_id,
            subject_hash=_subject_digest(namespace),
            external=bool(external_id),
        )

    principal = getattr(request, "user", None)
    principal_id = str(getattr(principal, "pk", "") or getattr(principal, "id", ""))
    if getattr(principal, "is_service_account_principal", False):
        principal_type = "service_account"
    elif getattr(principal, "is_authenticated", False):
        principal_type = "user"
    else:
        principal_type = "anonymous"
    tenant_id = str(getattr(request, "tenant_id", "") or "")
    return RequestSubject(
        principal_type=principal_type,
        principal_id=principal_id,
        subject_hash=_subject_digest(f"{tenant_id}|{principal_type}|{principal_id}"),
    )


def hash_token(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def subject_digest(value: str) -> str:
    return _subject_digest(value)


def _subject_digest(value: str) -> str:
    secret = str(getattr(settings, "NEXUS_AGENT_SUBJECT_HMAC_KEY", "") or settings.SECRET_KEY)
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


__all__ = ["RequestSubject", "hash_token", "request_subject", "subject_digest"]
