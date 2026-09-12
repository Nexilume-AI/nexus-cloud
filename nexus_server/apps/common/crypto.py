from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def encrypt_secret(value: str | None) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str | None) -> str:
    if not value:
        return ""
    return _fernet().decrypt(value.encode("ascii")).decode("utf-8")


def _fernet() -> MultiFernet:
    configured = [
        item.strip().encode("ascii")
        for item in str(getattr(settings, "NEXUS_SECRET_ENCRYPTION_KEYS", "")).split(",")
        if item.strip()
    ]
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    legacy_key = base64.urlsafe_b64encode(digest)
    keys = configured or [legacy_key]
    if configured and legacy_key not in keys:
        # Existing ciphertext remains readable while the first configured key
        # becomes the only key used for new encryption.
        keys.append(legacy_key)
    try:
        return MultiFernet([Fernet(key) for key in keys])
    except (TypeError, ValueError) as exc:
        raise ImproperlyConfigured(
            "NEXUS_SECRET_ENCRYPTION_KEYS must contain comma-separated Fernet keys."
        ) from exc
