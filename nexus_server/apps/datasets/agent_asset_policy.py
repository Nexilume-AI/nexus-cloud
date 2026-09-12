"""Explicit distribution policy for archiving Agent-owned data."""
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


@lru_cache(maxsize=16)
def _backend(path):
    try:
        backend = import_string(path)()
    except (ImportError, AttributeError, TypeError, ValueError):
        raise ImproperlyConfigured("Agent asset policy is not configured correctly.") from None
    if not all(callable(getattr(backend, method, None)) for method in ("get_mutable_agent", "check_export_source")):
        raise ImproperlyConfigured("Agent asset policy is incomplete.")
    return backend


def asset_policy():
    return _backend(getattr(settings, "NEXUS_AGENT_ASSET_POLICY_BACKEND", ""))


def get_mutable_agent(*, request, agent_id):
    return asset_policy().get_mutable_agent(request=request, agent_id=agent_id)


def check_export_source(*, request, agent, run=None, artifact=None, memory_items=None):
    return asset_policy().check_export_source(request=request, agent=agent,
        run=run, artifact=artifact, memory_items=memory_items)
