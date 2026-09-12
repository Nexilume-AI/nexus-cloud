"""Required distribution media ownership policy; never an implicit allow."""
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


OPERATIONS = ("media_request_tenant", "media_creation_fields",
              "visible_media_assets", "get_mutable_media_asset")


@lru_cache(maxsize=16)
def _backend(path):
    try:
        backend = import_string(path)()
    except (ImportError, AttributeError, TypeError, ValueError):
        raise ImproperlyConfigured("Nexus media policy is not configured correctly.") from None
    if not all(callable(getattr(backend, name, None)) for name in OPERATIONS):
        raise ImproperlyConfigured("Nexus media policy is incomplete.")
    return backend


def invoke_media_policy(operation, **kwargs):
    if operation not in OPERATIONS:
        raise ImproperlyConfigured("Unknown media policy operation.")
    return getattr(_backend(getattr(settings, "NEXUS_MEDIA_POLICY_BACKEND", "")), operation)(**kwargs)
