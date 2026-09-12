"""Authorization extension point shared by the personal core and commercial host.

The selected backend is operator configuration, never request input. Missing or
broken commercial extensions must NOT silently become a personal-mode allow.
"""
from functools import lru_cache
from typing import Protocol

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


class AuthorizationBackend(Protocol):
    def has_permission(self, user, tenant, action: str, resource_type=None, resource_id=None) -> bool: ...


@lru_cache(maxsize=16)
def _backend(path: str) -> AuthorizationBackend:
    if not path:
        raise ImproperlyConfigured("Nexus authorization backend is not configured.")
    try:
        instance = import_string(path)()
    except (ImportError, AttributeError, TypeError) as exc:
        raise ImproperlyConfigured("Nexus authorization backend could not be loaded.") from exc
    if not callable(getattr(instance, "has_permission", None)):
        raise ImproperlyConfigured("Nexus authorization backend does not implement has_permission.")
    return instance


def has_nexus_permission(user, tenant, action: str, resource_type=None, resource_id=None) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    backend = _backend(getattr(settings, "NEXUS_AUTHORIZATION_BACKEND", ""))
    # Only an explicit boolean grant authorizes an operation. Unexpected plugin
    # return values and errors cannot grant access accidentally.
    return backend.has_permission(user, tenant, action, resource_type, resource_id) is True
