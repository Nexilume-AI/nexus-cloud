from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import models


SENSITIVE_FIELD_TOKENS = (
    "password",
    "secret",
    "token",
    "credential",
    "encrypted",
    "private_key",
    "api_key",
    "provider_key",
    "hash",
)
SAFE_FIELD_NAMES = {"key_prefix"}

RESOURCE_FIELDS = {
    "api_key": [
        "id",
        "tenant_id",
        "project_id",
        "name",
        "key_prefix",
        "status",
        "created_by_id",
        "last_used_at",
        "deleted_at",
    ],
    "provider_account": [
        "id",
        "tenant_id",
        "provider_id",
        "account_id",
        "url",
        "auth_mode",
        "login_status",
        "last_login_error",
        "last_login_at",
        "pricing_rate",
        "status",
        "quota_status",
        "quota_reset_at",
        "quota_remaining_tokens",
        "quota_remaining_requests",
        "created_by_id",
        "deleted_at",
    ],
    "deployment": [
        "id",
        "tenant_id",
        "provider_id",
        "provider_account_id",
        "deployment_id",
        "model",
        "endpoint",
        "visibility",
        "team_id",
        "project_id",
        "pricing_rate",
        "health_status",
        "health_reason",
        "consecutive_failures",
        "last_latency_ms",
        "status",
        "created_by_id",
        "deleted_at",
    ],
}


def snapshot_resource(resource_type: str, instance: models.Model, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    fields = RESOURCE_FIELDS.get(resource_type)
    if fields is None:
        raise ValueError(f"Unsupported audit snapshot resource type: {resource_type}")
    return snapshot_model(instance, fields=fields, extra=extra)


def snapshot_model(instance: models.Model, *, fields: list[str], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for field in fields:
        if not hasattr(instance, field):
            continue
        value = getattr(instance, field)
        if is_sensitive_field(field):
            data[field] = {"redacted": True, "present": bool(value)}
        else:
            data[field] = normalize_value(value)
    if extra:
        for key, value in extra.items():
            if is_sensitive_field(key):
                data[key] = {"redacted": True, "present": bool(value)}
            else:
                data[key] = normalize_value(value)
    return data


def is_sensitive_field(name: str) -> bool:
    lowered = name.lower()
    if lowered in SAFE_FIELD_NAMES:
        return False
    return any(token in lowered for token in SENSITIVE_FIELD_TOKENS)


def normalize_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, models.Model):
        return str(value.pk)
    if isinstance(value, dict):
        return {str(key): normalize_value(item) for key, item in value.items() if not is_sensitive_field(str(key))}
    if isinstance(value, (list, tuple, set)):
        return [normalize_value(item) for item in value]
    return str(value)
