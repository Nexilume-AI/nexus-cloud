"""One operational fact source for owner HTTP requests and background monitoring."""
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Q
from rest_framework.exceptions import PermissionDenied, ValidationError
from apps.accounts.models import AccountProfile
from apps.metrics.models import AlertRule
from apps.metrics.registry import canonical, definitions as registry_definitions, ALIASES
from .services import installation_context
from .monitoring_window import window_metrics


def installation():
    from apps.tenancy.models import Tenant
    owner_id, tenant_id, project_id = installation_context()
    if not AccountProfile.objects.filter(user_id=owner_id, status="active", tenant_id=tenant_id, project_id=project_id).exists():
        raise ImproperlyConfigured("Personal monitoring owner context is unavailable.")
    return get_user_model().objects.get(pk=owner_id, is_active=True), Tenant.objects.get(pk=tenant_id, status="active"), project_id


def definitions(include_financial=False):
    return registry_definitions()


def resource_rows(model, tenant, project, owner):
    rows = model.objects.filter(tenant=tenant, project_id=project).exclude(status="deleted")
    if "created_by" in {field.name for field in model._meta.fields}:
        rows = rows.filter(Q(created_by=owner) | Q(created_by__isnull=True))
    return rows


def alert_rows(tenant, project, owner, *, include_deleted=False):
    allowed = {row["metric"] for row in definitions()}
    allowed.update(alias for alias, name in ALIASES.items() if name in allowed)
    rows = AlertRule.objects.filter(tenant=tenant, project_id=project, metric__in=allowed)
    rows = rows.filter(Q(created_by=owner) | Q(created_by__isnull=True))
    return (rows if include_deleted else rows.exclude(status="deleted")).order_by("-created_at")


def gauges(tenant, project, owner, *, rules=None):
    from apps.agents.models import Agent
    from apps.datasets.models import Dataset
    from apps.datasets.operations import operational_metrics
    from apps.deployments.models import Deployment, ModelGroup
    from apps.routers.models import Router
    rules = rules if rules is not None else alert_rows(tenant, project, owner)
    return {"counts": {**{name: resource_rows(model, tenant, project, owner).count()
        for name, model in (("agents", Agent), ("datasets", Dataset), ("routers", Router),
                            ("model_deployments", Deployment), ("model_groups", ModelGroup))},
        "alerts": rules.count()}, "data_assets": operational_metrics(tenant, project_id=project)}


def resolve_rule(rule, *, now=None, aggregate_cache=None):
    owner, tenant, project = installation()
    if (rule.tenant_id != tenant.pk or str(rule.project_id) != project
            or (rule.created_by_id is not None and rule.created_by_id != owner.pk)):
        raise PermissionDenied("Monitoring rule does not belong to this installation.")
    name = canonical(rule.metric)
    definition = next((item for item in definitions() if item["metric"] == name), None)
    if definition is None or (rule.resource_type or "system") not in definition["resource_types"]:
        raise ValidationError("UNKNOWN_METRIC")
    cache = aggregate_cache if aggregate_cache is not None else {}
    if definition["kind"] == "window":
        key = (tenant.pk, project, rule.window_seconds, rule.resource_type, rule.resource_id, now)
        if key not in cache:
            cache[key] = window_metrics(tenant, project_id=project, seconds=rule.window_seconds, end=now,
                resource_type=rule.resource_type, resource_id=rule.resource_id)
        data = cache[key]
        if name.endswith(("p95_latency_ms", "p99_latency_ms")):
            value = data["surfaces"]["agent" if rule.resource_type == "agent" else "gateway"][name.rsplit(".", 1)[-1]]
        else:
            category, metric = name.removeprefix("system.").split(".")
            value = data[category][metric]
    else:
        if rule.resource_id:
            raise ValidationError("INVALID_METRIC_SCOPE")
        key = (tenant.pk, project, "gauges")
        if key not in cache:
            cache[key] = gauges(tenant, project, owner)
        category, metric = name.removeprefix("system.").split(".")
        value = cache[key][category][metric]
    if value is None:
        raise ValidationError("NO_DATA")
    return Decimal(str(value))
