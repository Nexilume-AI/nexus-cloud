"""Nonfinancial invocation metrics from bounded timestamp windows.

Production PostgreSQL computes quantiles with ordered-set aggregates. SQLite
hosts use the same continuous interpolation over at most two selected values,
never materializing an entire invocation history in Python.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from math import floor, ceil
from django.db import connections
from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from .operational_registry import invocation_queries, Percentile


def period(*, start=None, end=None, seconds=300):
    if type(seconds) is not int or not 1 <= seconds <= 604800:
        raise ValidationError({"window_seconds": "Use an integer window from 1 second to 7 days."})
    end = timezone.now() if end is None else end
    if not isinstance(end, datetime) or timezone.is_naive(end):
        raise ValidationError({"period_end": "Use a timezone-aware timestamp."})
    start = end - timedelta(seconds=seconds) if start is None else start
    if not isinstance(start, datetime) or timezone.is_naive(start):
        raise ValidationError({"period_start": "Use a timezone-aware timestamp."})
    if not timedelta(0) < end - start <= timedelta(days=7):
        raise ValidationError({"window_seconds": "The end must follow the start by at most 7 days."})
    return start, end


def _quantile(queryset, count, fraction):
    if not count:
        return None
    position = (count - 1) * fraction
    lower, upper = floor(position), ceil(position)
    values = list(queryset.order_by("latency_ms", "pk").values_list("latency_ms", flat=True)[lower:upper + 1])
    if not values:
        return None
    return values[0] + (values[-1] - values[0]) * (position - lower)


def totals(queryset, *, gateway=False):
    expressions = dict(requests=Count("pk"), failed=Count("pk", filter=Q(status="failed")),
        avg_latency_ms=Avg("latency_ms"))
    if gateway:
        expressions.update(tokens=Sum("total_tokens"), fallbacks=Sum("fallback_count"))
    native = connections[queryset.db].vendor == "postgresql"
    if native:
        expressions.update(p95_latency_ms=Percentile("latency_ms", .95), p99_latency_ms=Percentile("latency_ms", .99))
    data = queryset.aggregate(**expressions)
    if not native:
        data.update(p95_latency_ms=_quantile(queryset, data["requests"], .95),
                    p99_latency_ms=_quantile(queryset, data["requests"], .99))
    return data


def window_metrics(tenant, *, start=None, end=None, seconds=300, project_id="", resource_type="", resource_id=""):
    start, end = period(start=start, end=end, seconds=seconds)
    gateway, agents = invocation_queries(tenant, start, end, project_id=project_id,
        resource_type=resource_type, resource_id=resource_id)
    g, a = totals(gateway, gateway=True), totals(agents)
    requests, failed = g["requests"] + a["requests"], g["failed"] + a["failed"]
    return {"period_start": start.isoformat(), "period_end": end.isoformat(), "source": "invocation_logs",
        "usage": {"requests": requests, "tokens": g["tokens"] or 0},
        "quality": {"failed": failed, "error_rate": float(Decimal(failed) * 100 / requests) if requests else None,
                    "fallbacks": g["fallbacks"] or 0},
        "surfaces": {"gateway": g, "agent": a}, "has_samples": requests > 0,
        "scope": "project" if project_id else "organization"}
