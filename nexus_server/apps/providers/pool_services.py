"""Compatibility names for host-selected commercial Provider pool services.

Personal Provider health checks live separately and never load this catalog.
No publication, moderation, pricing or Marketplace ranking lives in this module.
"""
from importlib import import_module
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


EXPORTS = frozenset(('active_public_pool_contributions', 'attach_marketplace_snapshots', 'filter_marketplace_items', 'get_public_pool_contribution', 'get_public_provider_runtime', 'health_score', 'latency_score', 'list_my_pool_contributions', 'list_pool_usages', 'list_public_pool_contributions', 'list_public_provider_runtimes', 'marketplace_available_quota_tokens', 'marketplace_snapshot', 'marketplace_stats_snapshot', 'marketplace_trust_score', 'marketplace_trust_tier', 'masked_tenant_name', 'moderate_pool_contribution', 'moderation_snapshot', 'parse_float_query', 'parse_int_query', 'percentile_95', 'pool_remaining_tokens', 'published_public_pool_contributions', 'quota_score', 'refresh_marketplace_stats_for_contribution', 'require_platform_admin', 'sort_marketplace_items'))


def __getattr__(name):
    if name == "record_runtime_health_check":
        from .health_services import record_runtime_health_check
        return record_runtime_health_check
    if name not in EXPORTS and name != "MARKETPLACE_WINDOW_DAYS":
        raise AttributeError(name)
    path = getattr(settings, "NEXUS_PROVIDER_POOL_SERVICES_MODULE", "")
    if not isinstance(path, str) or not path or path == __name__:
        raise ImproperlyConfigured("Provider pool services are not configured.")
    try:
        module = import_module(path)
    except ImportError:
        raise ImproperlyConfigured("Provider pool services could not be loaded.") from None
    if not all(callable(getattr(module, operation, None)) for operation in EXPORTS):
        raise ImproperlyConfigured("Provider pool services are incomplete.")
    return getattr(module, name)
