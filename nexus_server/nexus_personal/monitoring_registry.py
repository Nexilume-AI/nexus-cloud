"""Fixed-instance registry, composed without commercial definitions or services."""
from apps.metrics.operational_registry import DEFINITIONS, ALIASES, canonical, definitions, validate


def window_metrics(tenant, *, start=None, end=None, seconds=300, project_id="", resource_type="", resource_id=""):
    from apps.metrics.window_host import configured_window
    return configured_window()(tenant, start=start, end=end, seconds=seconds,
        project_id=project_id, resource_type=resource_type, resource_id=resource_id)


def resolve(rule, *, now=None, aggregate_cache=None):
    from .monitoring_facts import resolve_rule
    return resolve_rule(rule, now=now, aggregate_cache=aggregate_cache)
