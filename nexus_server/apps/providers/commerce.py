"""Explicit distribution boundary for commercial Provider pool settlement.

Missing settlement is an error, never a fabricated zero-price receipt. Personal
model execution must use its own operational accounting path, not these APIs.
"""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


OPERATIONS = frozenset({
    "provider_pricing_snapshot", "provider_usage_raw_amount", "settle_provider_usage_charge",
    "record_pool_usage", "record_failed_pool_usage",
    "share_provider_runtime", "get_provider_runtime_marketplace_offer", "unshare_provider_runtime",
    "ensure_pool_contribution", "reserve_provider_capacity", "release_provider_capacity",
    "mark_provider_capacity_used", "settle_provider_capacity",
})


def invoke_provider_commerce(operation, *args, **kwargs):
    if operation not in OPERATIONS:
        raise ImproperlyConfigured("Unknown Provider commerce operation.")
    path = getattr(settings, "NEXUS_PROVIDER_COMMERCE_BACKEND", "")
    try:
        backend = import_string(path)()
    except (ImportError, AttributeError, TypeError, ValueError):
        raise ImproperlyConfigured("Provider commerce backend is not configured.") from None
    if not all(callable(getattr(backend, name, None)) for name in OPERATIONS):
        raise ImproperlyConfigured("Provider commerce backend is incomplete.")
    return getattr(backend, operation)(*args, **kwargs)
