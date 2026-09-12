"""Instance-owned Data Asset alerts, without organization-wide defaults."""
from decimal import Decimal
from django.db import transaction
from django.db.models import Q
from rest_framework.exceptions import PermissionDenied
from apps.metrics.models import AlertRule
from apps.tenancy.models import Tenant
from .monitoring_facts import installation


@transaction.atomic
def ensure_operational_alerts(tenant):
    owner, current, project = installation()
    if tenant.pk != current.pk:
        raise PermissionDenied("Monitoring context does not belong to this installation.")
    Tenant.objects.select_for_update(no_key=True).get(pk=current.pk)
    for metric, threshold, operator in (
        ("queue_age_seconds", 300, "gte"), ("stalled", 1, "gte"),
        ("cleanup_backlog", 1, "gte"), ("failed_24h", 1, "gte"),
        ("spool_free_bytes", 1024 ** 3, "lt"),
    ):
        rows = AlertRule.objects.filter(tenant=current, project_id=project,
            resource_type="system", resource_id="", metric=f"data_assets.{metric}")
        rows = rows.filter(Q(created_by=owner) | Q(created_by__isnull=True))
        # Keep all deliberate states, including deleted/muted/customized rules.
        # A historical organization-scoped row is never silently adopted.
        if not rows.exists():
            AlertRule.objects.create(tenant=current, project_id=project, created_by=owner,
                resource_type="system", resource_id="", metric=f"data_assets.{metric}",
                threshold=str(threshold), threshold_value=Decimal(threshold),
                operator=operator, cooldown_seconds=3600)


def maintain_data_assets():
    from apps.datasets.models import DatasetTransfer
    from apps.datasets.transfers import cleanup_expired_writes
    owner, tenant, project = installation()
    ensure_operational_alerts(tenant)
    transfers = DatasetTransfer.objects.filter(tenant=tenant, dataset__tenant=tenant,
        dataset__project_id=project).filter(Q(dataset__created_by=owner) | Q(dataset__created_by__isnull=True))
    return {"cleaned": cleanup_expired_writes(queryset=transfers)}
