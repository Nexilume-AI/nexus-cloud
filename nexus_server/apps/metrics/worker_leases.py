"""Shared durable lease primitives; authority is selected before claiming work."""
from datetime import timedelta
import uuid
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import MonitoringHeartbeat


@transaction.atomic
def claim_component(tenant, component, now):
    MonitoringHeartbeat.objects.get_or_create(tenant=tenant, component=component)
    row = MonitoringHeartbeat.objects.select_for_update(skip_locked=True).filter(tenant=tenant, component=component).first()
    if row is None or (row.lease_until and row.lease_until > now):
        return None
    row.lease_id = str(uuid.uuid4())
    row.lease_until = now + timedelta(minutes=5)
    row.last_started_at = now
    row.save(update_fields=["lease_id", "lease_until", "last_started_at"])
    return row.lease_id


def finish_component(tenant, component, lease, error=""):
    if lease is None:
        return
    fields = {"lease_until": None, "lease_id": "", "last_error_code": error}
    if not error:
        fields["last_success_at"] = timezone.now()
    MonitoringHeartbeat.objects.filter(tenant=tenant, component=component, lease_id=lease).update(**fields)


@transaction.atomic
def claim_delivery(model, tenant, now, *, queryset=None):
    manager = queryset if queryset is not None else model.objects
    if manager.model is not model:
        raise ValueError("Delivery queryset model mismatch.")
    row = manager.select_for_update(skip_locked=True).filter(tenant=tenant,
        delivery_status="pending", next_attempt_at__lte=now).exclude(delivery_key__isnull=True).filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now)).order_by("next_attempt_at", "pk").first()
    if row is None:
        return None
    row.lease_id = str(uuid.uuid4())
    row.lease_until = now + timedelta(seconds=60)
    row.attempts += 1
    row.save(update_fields=["lease_id", "lease_until", "attempts", "updated_at"])
    return row
