"""Explicit Agent catalog/commercial composition; no implicit personal fallback."""
from functools import lru_cache
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


OPERATIONS = (
    "list_agents", "visible_agents", "set_publication", "set_visibility", "set_pricing",
    "validate_agent_publication", "list_marketplace_agents", "get_marketplace_agent",
    "get_public_agent_display", "get_display_agent", "public_agent_summary",
    "clone_commercial_configuration",
    "_get_bindable_agent", "validate_agent_update", "deploy_agent", "create_agent_key",
    "submit_public_display_message", "public_demo_cookie_name",
    "get_public_display_run", "get_public_display_asset",
    "start_public_agent_demo", "_execute_public_demo",
    "expire_stale_non_invocation_runs", "interaction_run_kinds",
    "validate_run_interaction", "run_display_asset_url",
)


@lru_cache(maxsize=16)
def _backend(path):
    try:
        backend = import_string(path)()
    except (ImportError, AttributeError, TypeError, ValueError):
        raise ImproperlyConfigured("Nexus Agent policy backend is not configured correctly.") from None
    if not all(callable(getattr(backend, operation, None)) for operation in OPERATIONS):
        raise ImproperlyConfigured("Nexus Agent policy backend is incomplete.")
    return backend


def invoke_agent_policy(operation, **kwargs):
    if operation not in OPERATIONS:
        raise ImproperlyConfigured("Unknown Agent policy operation.")
    return getattr(_backend(getattr(settings, "NEXUS_AGENT_POLICY_BACKEND", "")), operation)(**kwargs)
