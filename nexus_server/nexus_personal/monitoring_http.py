"""Actual owner-scoped operational monitoring, with no financial data source.

These APIs do not run collectors or deliver email. Durable heartbeat/outbox
records describe those workers truthfully, including missing or stale workers.
"""
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.audit.services import log_audit
from apps.metrics.models import AlertEvent, AlertNotification, AlertRule, MonitoringHeartbeat
from apps.metrics.registry import canonical, validate
from .monitoring_policy import context, capabilities
from .monitoring_window import window_metrics


from .monitoring_facts import definitions


def _identifier(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise NotFound("Monitoring resource not found.") from None


def _seconds(request):
    try:
        seconds = int(request.query_params.get("window_seconds", 86400))
        if not 60 <= seconds <= 604800:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValidationError({"window_seconds": "Use 60–604800 seconds."}) from None
    return seconds


def _resources(model, request, *, scope=None):
    from .monitoring_facts import resource_rows
    tenant, project = scope if scope is not None else context(request)
    return resource_rows(model, tenant, project, request.user)


def _gauges(request, *, scope=None, rules=None):
    from .monitoring_facts import gauges
    tenant, project = scope if scope is not None else context(request)
    return gauges(tenant, project, request.user, rules=rules)


def _monitoring_health(tenant):
    now = timezone.now()
    rows = {row.component: row for row in MonitoringHeartbeat.objects.filter(tenant=tenant)}
    states, successes, affected = [], [], []
    for name, interval, area in (("collector", 300, "metrics"), ("evaluator", 60, "alerts"),
                                 ("delivery", 30, "notifications")):
        row = rows.get(name)
        last = row.last_success_at if row else None
        age = (now - last).total_seconds() if last else None
        limit = max(60, int(getattr(settings, f"NEXUS_MONITOR_{name.upper()}_SECONDS", interval)) * 3)
        state = "unknown" if age is None else "stale" if age > limit else "healthy"
        if row and row.last_error_code:
            state = "degraded"
        states.append(state)
        successes.append(last.isoformat() if last else None)
        if state != "healthy":
            affected.append(area)
    state = next((value for value in ("unknown", "stale", "degraded") if value in states), "healthy")
    test_backend = settings.EMAIL_BACKEND in {
        f"django.core.mail.backends.{name}.EmailBackend" for name in ("locmem", "console", "dummy", "filebased")}
    if test_backend:
        if "notifications" not in affected:
            affected.append("notifications")
        if state == "healthy":
            state = "degraded"
    configured = getattr(settings, "NEXUS_MONITOR_SMTP_CONFIGURED", True)
    if not configured:
        state = "unknown"
        if "notifications" not in affected:
            affected.append("notifications")
    return {"state": state, "as_of": now.isoformat(),
        "last_updated_at": min(successes) if all(successes) else None,
        "affected_areas": affected,
        "delivery_notice": "Configure SMTP in the protected host configuration." if not configured else
            "Email transport does not deliver to external recipients." if test_backend else
            "SMTP acceptance does not confirm recipient delivery."}


def _summary(request, seconds, *, scope=None, rules=None):
    from apps.jobs.models import Job
    tenant, project = scope if scope is not None else context(request)
    rules = rules if rules is not None else _alert_rows(tenant, project, request.user)
    events = AlertEvent.objects.filter(tenant=tenant, rule__in=rules, is_test=False, status="firing")
    jobs = Job.objects.filter(tenant=tenant, project_id=project)
    counts = jobs.aggregate(
        failed_jobs=Count("pk", filter=Q(status="failed", created_at__gte=timezone.now() - timedelta(seconds=seconds))),
        running_jobs=Count("pk", filter=Q(status="running")), queued_jobs=Count("pk", filter=Q(status="queued")))
    return {**counts, "window_seconds": seconds, "as_of": timezone.now().isoformat(),
        "firing_alerts": events.count(), "unacknowledged_alerts": events.filter(acknowledged_at__isnull=True).count(),
        "failed_deliveries": AlertNotification.objects.filter(tenant=tenant, event__rule__in=rules, delivery_status="failed").count(),
        "rule_evaluation_errors": rules.exclude(last_error_code="").count(), "rule_count": rules.count()}


def get_system_metrics(*, request):
    from apps.gateway.models import GatewayRequestLog
    from apps.agents.models import AgentRuntimeInvocation
    tenant, project = context(request)
    seconds = _seconds(request)
    # Reuse validated context only within this request's aggregate helpers;
    # never cache authority on the request, across requests or in shared cache.
    scope = tenant, project
    rules = _alert_rows(tenant, project, request.user)
    gateway = GatewayRequestLog.objects.filter(tenant=tenant, project_id=project).aggregate(requests=Count("pk"), tokens=Sum("total_tokens"))
    agents = AgentRuntimeInvocation.objects.filter(tenant=tenant, project_id=project, status__in=("success", "failed")).count()
    return {"resource_type": "system", "tenant_id": str(tenant.pk), "scope": "project",
        **_gauges(request, scope=scope, rules=rules), "usage": {"requests": gateway["requests"] + agents, "tokens": gateway["tokens"] or 0},
        "window": window_metrics(tenant, project_id=project, seconds=seconds),
        "summary": _summary(request, seconds, scope=scope, rules=rules), "monitoring": _monitoring_health(tenant),
        "capabilities": capabilities(request), "as_of": timezone.now().isoformat(), "max_age_seconds": 0}


def get_resource_metrics(*, request, resource_type, resource_id):
    from apps.agents.models import Agent, AgentLog, AgentRuntimeInvocation
    from apps.datasets.models import Dataset
    from apps.deployments.models import Deployment, ModelGroup
    from apps.gateway.models import GatewayRequestLog
    from apps.routers.models import Router, RouterRuntimeInvocation
    tenant, project = context(request)
    if resource_type not in {"agent", "router", "dataset", "model"}:
        raise ValidationError({"resource_type": "Select Agent, Router, Dataset or Model operational metrics."})
    if not resource_id:
        raise ValidationError({"id": "A resource ID is required."})
    data = {"resource_type": resource_type, "resource_id": str(resource_id), "tenant_id": str(tenant.pk)}
    if resource_type == "model":
        # Compatibility permits deployment identifiers and group names, but
        # every lookup still uses the installation's actual owner and Project.
        deployments = _resources(Deployment, request, scope=(tenant, project)).select_related("provider")
        groups = _resources(ModelGroup, request, scope=(tenant, project))
        try:
            identifier = UUID(str(resource_id))
        except (ValueError, TypeError, AttributeError):
            deployment = deployments.filter(deployment_id=resource_id).first()
            group = groups.filter(name=resource_id).first()
        else:
            deployment = deployments.filter(pk=identifier).first()
            group = groups.filter(pk=identifier).first()
        if deployment is None and group is None:
            raise NotFound("Monitoring resource not found.")
        return {**data, "deployment": {"id": str(deployment.pk), "deployment_id": deployment.deployment_id,
            "model": deployment.model, "status": deployment.status, "provider": deployment.provider.name} if deployment else None,
            "model_group": {"id": str(group.pk), "name": group.name, "status": group.status,
                "deployment_count": group.deployment_links.count()} if group else None}
    model = {"agent": Agent, "router": Router, "dataset": Dataset}[resource_type]
    row = _resources(model, request, scope=(tenant, project)).filter(pk=_identifier(resource_id)).first()
    if row is None:
        raise NotFound("Monitoring resource not found.")
    data.update(status=row.status, current_version=row.current_version,
        versions=row.versions.exclude(status="deleted").count())
    if resource_type == "dataset":
        return {**data, "visibility": row.visibility, "size_bytes": row.size_bytes, "file_count": row.file_count}
    data["deployments"] = dict(row.deployments.values("status").annotate(count=Count("pk")).values_list("status", "count"))
    if resource_type == "agent":
        invocations = AgentRuntimeInvocation.objects.filter(tenant=tenant, project_id=project, agent=row)
        data.update(visibility=row.visibility, logs=AgentLog.objects.filter(agent=row).count(),
            runtime_deployments=dict(row.runtime_deployments.values("status").annotate(count=Count("pk")).values_list("status", "count")))
    else:
        invocations = GatewayRequestLog.objects.filter(tenant=tenant, project_id=project, router=row)
        data.update(strategy=row.strategy, bindings=row.model_group_bindings.exclude(status="deleted").count())
    data["runtime"] = invocations.aggregate(request_count=Count("pk"),
        success_count=Count("pk", filter=Q(status="success")), failed_count=Count("pk", filter=Q(status="failed")))
    if resource_type == "router":
        custom = RouterRuntimeInvocation.objects.filter(tenant=tenant, router=row)
        data["runtime"].update(custom.aggregate(custom_invocation_count=Count("pk"),
            custom_success_count=Count("pk", filter=Q(status="success")), custom_failed_count=Count("pk", filter=Q(status="failed"))))
        totals = invocations.aggregate(total_tokens=Sum("total_tokens"), fallback_count=Sum("fallback_count"))
        data["runtime"].update({key: value or 0 for key, value in totals.items()})
    data["window"] = window_metrics(tenant, project_id=project, resource_type=resource_type,
        resource_id=resource_id, seconds=_seconds(request))
    return data


def list_alerts(*, request, include_deleted=False):
    tenant, project = context(request)
    return _alert_rows(tenant, project, request.user, include_deleted=include_deleted)


from .monitoring_facts import alert_rows as _alert_rows


def action_target(*, request, kind):
    from apps.metrics.serializers import AlertRuleSerializer, AlertEventSerializer, AlertNotificationSerializer
    tenant, _ = context(request, "metrics.manage")
    if kind == "rules":
        return list_alerts(request=request), AlertRuleSerializer
    if kind == "incidents":
        return list_alert_events(request=request), AlertEventSerializer
    if kind == "notifications":
        # Revalidate active rule ownership before making a failed delivery
        # eligible again; never resurrect a revoked/deleted rule's outbox.
        return AlertNotification.objects.filter(tenant=tenant, event__tenant=tenant,
            event__rule__in=list_alerts(request=request).filter(status="active")), AlertNotificationSerializer
    raise NotFound()


def _audit(request, action, rule, **metadata):
    log_audit(request=request, actor=request.user, action=action, resource_type="alert_rule",
        resource_id=rule.pk, metadata={"metric": rule.metric, **metadata})


@transaction.atomic
def create_alert(*, request, metric, threshold, operator="gte", resource_type="", resource_id="",
                 window_seconds=300, cooldown_seconds=300, notification_channels=None):
    tenant, project = context(request, "metrics.manage")
    name = canonical(metric)
    catalog = {row["metric"]: row for row in definitions()}
    if name not in catalog or (resource_type or "system") not in catalog[name]["resource_types"]:
        raise ValidationError({"metric": "Select an available operational metric and scope."})
    value, unit = validate(metric=name, threshold=threshold, resource_type=resource_type,
        window_seconds=window_seconds, notification_channels=notification_channels)
    if resource_type not in ("", "system"):
        get_resource_metrics(request=request, resource_type=resource_type, resource_id=resource_id)
    elif resource_id:
        raise ValidationError({"resource_id": "System metrics do not accept a resource ID."})
    if operator not in dict(AlertRule.OPERATOR_CHOICES):
        raise ValidationError({"operator": "Select an available operator."})
    rule = AlertRule.objects.create(tenant=tenant, project_id=project, created_by=request.user,
        metric=name, threshold=threshold, threshold_value=value, threshold_unit=unit, operator=operator,
        resource_type=resource_type, resource_id=resource_id, window_seconds=window_seconds,
        cooldown_seconds=cooldown_seconds, notification_channels=notification_channels or {})
    _audit(request, "metrics.alert.create", rule)
    return rule


@transaction.atomic
def delete_alert(*, request, alert_id):
    context(request, "metrics.manage")
    rule = list_alerts(request=request).select_for_update().filter(pk=_identifier(alert_id)).first()
    if rule is None:
        raise NotFound("Alert not found.")
    rule.delete()
    _audit(request, "metrics.alert.delete", rule)
    return rule


def list_alert_events(*, request):
    tenant, _ = context(request)
    rows = AlertEvent.objects.filter(tenant=tenant, rule__in=list_alerts(request=request, include_deleted=True))
    if request.query_params.get("rule"):
        rows = rows.filter(rule_id=_identifier(request.query_params["rule"]))
    return rows.select_related("rule").prefetch_related("notifications").order_by("-triggered_at")


def get_alert_event(*, request, event_id):
    event = list_alert_events(request=request).filter(pk=_identifier(event_id)).first()
    if event is None:
        raise NotFound("Alert event not found.")
    return event


def _resolve(request, rule):
    from .monitoring_facts import resolve_rule
    context(request)
    return resolve_rule(rule)


@transaction.atomic
def test_alert(*, request, alert_id):
    context(request, "metrics.manage")
    rule = list_alerts(request=request).select_for_update().filter(pk=_identifier(alert_id)).first()
    if rule is None:
        raise NotFound("Alert not found.")
    value, now = _resolve(request, rule), timezone.now()
    event = AlertEvent.objects.create(tenant=rule.tenant, rule=rule, metric=rule.metric,
        resource_type=rule.resource_type, resource_id=rule.resource_id, value=value,
        threshold=rule.threshold, threshold_value=rule.threshold_value, triggered_at=now,
        resolved_at=now, status="resolved", is_test=True, metadata_json={"operator": rule.operator, "forced": True})
    from .monitoring_workers import enqueue_alert
    enqueue_alert(event)
    _audit(request, "metrics.alert.test", rule, event_id=str(event.pk))
    return event
