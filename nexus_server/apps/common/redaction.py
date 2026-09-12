"""Shared secret-redaction rules; no Agent, IAM or billing imports."""
from __future__ import annotations
import re
from typing import Any


REDACTION_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)


REDACTION_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}\b", re.IGNORECASE)


REDACTION_SECRET_RE = re.compile(r"\b(?:sk|pk|rk|ghp|github_pat|xoxb|xoxp|AKIA)[A-Za-z0-9_\-]{12,}\b")


REDACTION_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")


REDACTION_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
}


def redact_payload(value: Any) -> tuple[Any, dict[str, int]]:
    stats = {"replacement_count": 0, "sensitive_key_count": 0}

    def walk(item: Any, key: str = "") -> Any:
        normalized_key = key.lower().replace("-", "_")
        if normalized_key in REDACTION_SENSITIVE_KEYS:
            stats["sensitive_key_count"] += 1
            stats["replacement_count"] += 1
            return "[REDACTED]"
        if isinstance(item, dict):
            return {k: walk(v, str(k)) for k, v in item.items()}
        if isinstance(item, list):
            return [walk(child, key) for child in item]
        if isinstance(item, str):
            redacted = item
            for pattern in (REDACTION_BEARER_RE, REDACTION_SECRET_RE, REDACTION_EMAIL_RE, REDACTION_PHONE_RE):
                redacted, count = pattern.subn("[REDACTED]", redacted)
                stats["replacement_count"] += count
            return redacted
        return item

    return walk(value), stats
