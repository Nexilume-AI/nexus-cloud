"""Executable operational catalog and invocation queries, without finance."""
from django.db.models import Aggregate, FloatField
from rest_framework.exceptions import ValidationError


class Percentile(Aggregate):
    output_field = FloatField()
    template = "PERCENTILE_CONT(%(percentile)s) WITHIN GROUP (ORDER BY %(expressions)s)"

    def __init__(self, expression, percentile):
        super().__init__(expression, percentile=percentile)


DEFINITIONS = {
    "system.usage.requests": ("count", "window", "Completed Gateway requests plus Agent invocations"),
    "system.usage.tokens": ("tokens", "window", "Gateway tokens recorded in the window"),
    "system.quality.error_rate": ("%", "window", "Failed / completed invocations × 100"),
    "system.quality.p95_latency_ms": ("ms", "window", "P95 Gateway latency (Agent latency when scoped to an Agent)"),
    "system.quality.p99_latency_ms": ("ms", "window", "P99 Gateway latency (Agent latency when scoped to an Agent)"),
    "system.quality.fallbacks": ("count", "window", "Gateway source fallback attempts"),
    **{f"system.counts.{name}": ("count", "gauge", f"Current {name.replace('_', ' ')}")
       for name in ("datasets", "agents", "routers", "alerts")},
    **{f"data_assets.{name}": ("bytes" if "bytes" in name else "seconds" if "seconds" in name else "count", "gauge", f"Data Assets {name.replace('_', ' ')}")
       for name in ("queued", "running", "stalled", "queue_age_seconds", "failed_24h", "completed_24h", "cleanup_backlog", "reserved_write_bytes", "completed_downloads_24h", "expired_downloads_24h", "spool_free_bytes")},
}
ALIASES = {key.removeprefix("system."): key for key in DEFINITIONS if key.startswith("system.")}
ALIASES["error_rate"] = "system.quality.error_rate"


def canonical(metric):
    return ALIASES.get(metric, metric)


def definitions(include_financial=False):
    return [{"metric": name, "unit": unit, "kind": kind, "description": description,
             "resource_types": ["system", "agent", "router"] if kind == "window" else ["system"]}
        for name, (unit, kind, description) in DEFINITIONS.items()]


def validate(*, metric, threshold, resource_type="", window_seconds=300, notification_channels=None):
    from apps.metrics.metric_validation import validate_metric
    return validate_metric(metric=canonical(metric), threshold=threshold, resource_type=resource_type,
        window_seconds=window_seconds, notification_channels=notification_channels, catalog=DEFINITIONS,
        resource_types={"", "system", "agent", "router"})


def invocation_queries(tenant, start, end, *, project_id="", resource_type="", resource_id=""):
    if resource_type not in ("", "system", "agent", "router"):
        raise ValidationError({"resource_type": "Unsupported operational metric scope."})
    from apps.gateway.models import GatewayRequestLog
    from apps.agents.models import AgentRuntimeInvocation
    gateway = GatewayRequestLog.objects.filter(tenant=tenant, created_at__gte=start, created_at__lt=end)
    agents = AgentRuntimeInvocation.objects.filter(tenant=tenant, created_at__gte=start, created_at__lt=end, status__in=["success", "failed"])
    if project_id:
        gateway = gateway.filter(project_id=str(project_id))
        agents = agents.filter(project_id=project_id)
    if resource_type == "agent":
        gateway = gateway.none()
        agents = agents.filter(agent_id=resource_id)
    elif resource_type == "router":
        gateway = gateway.filter(router_id=resource_id)
        agents = agents.none()
    return gateway, agents
