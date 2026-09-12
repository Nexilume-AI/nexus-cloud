"""Explicit request-context resolution, separate from Organization membership."""
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


@lru_cache(maxsize=16)
def _backend(path):
    try:
        backend = import_string(path)()
    except (ImportError, AttributeError, TypeError, ValueError):
        raise ImproperlyConfigured("Nexus request context backend is not configured correctly.") from None
    if not callable(getattr(backend, "request_tenant", None)):
        raise ImproperlyConfigured("Nexus request context backend is incomplete.")
    return backend


def get_tenant_from_request(request):
    return _backend(getattr(settings, "NEXUS_REQUEST_CONTEXT_BACKEND", "")).request_tenant(request)
