"""Provider ownership discovery requires an explicit distribution policy."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


def provider_catalog():
    path = getattr(settings, "NEXUS_PROVIDER_CATALOG_BACKEND", "")
    try:
        backend = import_string(path)()
    except (ImportError, AttributeError, TypeError, ValueError):
        raise ImproperlyConfigured("Provider catalog policy is not configured.") from None
    if not all(callable(getattr(backend, name, None)) for name in ("filter_rows", "filter_queryset")):
        raise ImproperlyConfigured("Provider catalog policy is incomplete.")
    return backend
