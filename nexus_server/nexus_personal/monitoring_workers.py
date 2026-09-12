"""Durable operational workers for exactly one validated personal installation.

SMTP is at-least-once. Stable Message-ID/outbox keys do not promise recipient
delivery or exactly-once external effects. No financial report worker exists.
"""
from datetime import timedelta
import hashlib

from django.conf import settings
from django.core.mail import EmailMessage
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit.services import write_audit_log
from apps.metrics.models import AlertRule, AlertEvent, AlertNotification, MetricSnapshot
from apps.metrics.worker_leases import claim_component, finish_component, claim_delivery
from apps.metrics.registry import canonical
from .monitoring_facts import installation, gauges, definitions, resolve_rule
from .monitoring_window import window_metrics


@transaction.atomic
def _audit(tenant, action, kind, row, **metadata):
    _, own_tenant, project = installation()
    if own_tenant.pk != tenant.pk:
        raise ValidationError("MONITORING_CONTEXT_UNAVAILABLE")
    entry = write_audit_log(actor=None, tenant=tenant, action=action, resource_type=kind,
        resource_id=row.pk, metadata=metadata)
    # No fabricated HTTP request/owner impersonation for a system worker.
    # Bind its audit record to the validated installation in this transaction.
    entry.project_id = project
    entry.save(update_fields=["project_id"])


def _rules(tenant, project, owner):
    return AlertRule.objects.filter(tenant=tenant, project_id=project).filter(
        Q(created_by=owner) | Q(created_by__isnull=True))


def _notifications(tenant, project, owner):
    return AlertNotification.objects.filter(tenant=tenant, event__tenant=tenant,
        event__rule__in=_rules(tenant, project, owner))


def collect_snapshots():
    owner, tenant, project = installation()
    lease = claim_component(tenant, "collector", timezone.now())
    if not lease:
        return 0
    try:
        data = {"scope": "project", "project_id": project, **gauges(tenant, project, owner),
            "window": window_metrics(tenant, project_id=project, seconds=300)}
        with transaction.atomic():
            snapshot = MetricSnapshot.objects.create(tenant=tenant, resource_type="system", metrics_json=data)
            _audit(tenant, "metrics.snapshot.capture", "metric_snapshot", snapshot, resource_type="system")
    except Exception:
        finish_component(tenant, "collector", lease, "COLLECTION_FAILED")
        return 0
    finish_component(tenant, "collector", lease)
    return 1


def enqueue_alert(event, phase=None):
    owner, tenant, project = installation()
    current = AlertEvent.objects.select_related("rule", "rule__created_by").filter(
        pk=event.pk, tenant=tenant, rule__in=_rules(tenant, project, owner)).first()
    if current is None or canonical(current.metric) not in {row["metric"] for row in definitions()}:
        raise ValidationError("MONITORING_EVENT_UNAVAILABLE")
    phase = phase or ("test" if current.is_test else "firing")
    if phase not in ({"test"} if current.is_test else {"firing", "resolved"}):
        raise ValidationError("MONITORING_PHASE_INVALID")
    config = current.rule.notification_channels
    if not isinstance(config, dict):
        raise ValidationError("MONITORING_TARGET_INVALID")
    targets = config.get("emails", []) or [owner.email]
    if not isinstance(targets, list) or len(targets) > 10:
        raise ValidationError("MONITORING_TARGET_INVALID")
    targets = sorted({str(target).strip().lower() for target in targets if str(target).strip()})
    for target in targets:
        validate_email(target)
    rows = []
    for target in targets:
        key = hashlib.sha256(f"alert:{current.pk}:{phase}:{target}".encode()).hexdigest()
        row, _ = AlertNotification.objects.get_or_create(delivery_key=key, defaults={
            "tenant": tenant, "event": current, "target": target, "phase": phase})
        rows.append(row)
    return rows


def _compare(value, rule):
    if rule.threshold_value is None:
        raise ValidationError("INVALID_THRESHOLD")
    operations = {"gt": lambda: value > rule.threshold_value, "gte": lambda: value >= rule.threshold_value,
        "lt": lambda: value < rule.threshold_value, "lte": lambda: value <= rule.threshold_value,
        "eq": lambda: value == rule.threshold_value}
    if rule.operator not in operations:
        raise ValidationError("INVALID_OPERATOR")
    return operations[rule.operator]()


def evaluate_rules(*, now=None, metric_prefix=None):
    owner, tenant, project = installation()
    now = now or timezone.now()
    component = "evaluator" if not metric_prefix else "dataset_evaluator"
    lease = claim_component(tenant, component, now)
    result = {"evaluated": 0, "fired": 0, "resolved": 0, "failed": 0}
    if not lease:
        return result
    cache, failed = {}, False
    try:
        rules = _rules(tenant, project, owner).filter(status="active")
        if metric_prefix:
            rules = rules.filter(metric__startswith=metric_prefix)
        for rule_id in rules.values_list("pk", flat=True).iterator(chunk_size=100):
            with transaction.atomic():
                rule = rules.select_for_update(skip_locked=True, of=("self",)).select_related("tenant").filter(pk=rule_id).first()
                if rule is None:
                    continue
                result["evaluated"] += 1
                try:
                    value = resolve_rule(rule, now=now, aggregate_cache=cache)
                    firing = _compare(value, rule)
                except Exception as error:
                    rule.last_error_code = "NO_DATA" if "NO_DATA" in str(getattr(error, "detail", "")) else "METRIC_UNAVAILABLE"
                    rule.last_evaluated_at = now
                    rule.save(update_fields=["last_error_code", "last_evaluated_at", "updated_at"])
                    result["failed"] += 1
                    failed = True
                    continue
                open_events = AlertEvent.objects.filter(tenant=tenant, rule=rule, status="firing", is_test=False)
                if firing:
                    due = rule.last_triggered_at is None or now - rule.last_triggered_at >= timedelta(seconds=rule.cooldown_seconds)
                    if due and not open_events.exists() and not (rule.muted_until and rule.muted_until > now):
                        event = AlertEvent.objects.create(tenant=tenant, rule=rule, metric=rule.metric,
                            resource_type=rule.resource_type, resource_id=rule.resource_id, value=value,
                            threshold=rule.threshold, threshold_value=rule.threshold_value, triggered_at=now,
                            metadata_json={"operator": rule.operator, "forced": False})
                        enqueue_alert(event)
                        _audit(tenant, "metrics.alert_event.create", "alert_event", event, rule_id=str(rule.pk))
                        rule.last_triggered_at = now
                        result["fired"] += 1
                else:
                    for event in open_events:
                        event.status, event.resolved_at = "resolved", now
                        event.metadata_json = {**event.metadata_json, "resolved_value": str(value)}
                        event.save(update_fields=["status", "resolved_at", "metadata_json", "updated_at"])
                        enqueue_alert(event, "resolved")
                        _audit(tenant, "metrics.alert_event.resolve", "alert_event", event, rule_id=str(rule.pk))
                        result["resolved"] += 1
                rule.last_evaluated_at, rule.last_error_code = now, ""
                rule.save(update_fields=["last_evaluated_at", "last_triggered_at", "last_error_code", "updated_at"])
    except Exception:
        failed = True
        result["failed"] += 1
    finally:
        finish_component(tenant, component, lease, "RULE_EVALUATION_FAILED" if failed else "")
    return result


def _bound(value, maximum, name):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValidationError({name: f"Use 1–{maximum}."})
    return value


def deliver_notifications(*, limit=100, now=None):
    limit = _bound(limit, 1000, "limit")
    owner, tenant, project = installation()
    now = now or timezone.now()
    lease = claim_component(tenant, "delivery", now)
    result = {"sent": 0, "retrying": 0, "failed": 0}
    if not lease:
        return result
    error = ""
    if not getattr(settings, "NEXUS_MONITOR_SMTP_CONFIGURED", True):
        finish_component(tenant, "delivery", lease, "EMAIL_NOT_CONFIGURED")
        return result
    try:
        for _ in range(limit):
            row = claim_delivery(AlertNotification, tenant, now, queryset=_notifications(tenant, project, owner))
            if row is None:
                break
            # Re-read installation/owner and the rule immediately before dispatch.
            # An outbox row is not a grant and cannot resurrect a disabled rule.
            owner, tenant, project = installation()
            event = row.event
            rule = _rules(tenant, project, owner).filter(pk=event.rule_id, status="active").first()
            allowed = rule is not None and canonical(rule.metric) in {item["metric"] for item in definitions()}
            fields = {"lease_id": "", "lease_until": None}
            outcome = "failed"
            if not allowed:
                fields.update(delivery_status="failed", error_code="PERMISSION_OR_RULE_REVOKED", error_message="PERMISSION_OR_RULE_REVOKED")
            else:
                try:
                    validate_email(row.target)
                    message = EmailMessage(f"Nexus {row.phase}: {event.metric}",
                        f"Metric: {event.metric}\nValue: {event.value}\nThreshold: {event.threshold}\nState: {row.phase}\nIncident: {event.pk}",
                        getattr(settings, "NEXUS_REPORT_EMAIL_FROM", settings.DEFAULT_FROM_EMAIL), [row.target],
                        headers={"Message-ID": f"<{row.pk}@nexus-notifications>"})
                    if message.send(fail_silently=False) != 1:
                        raise RuntimeError("TRANSPORT_REJECTED")
                except Exception:
                    terminal = row.attempts >= int(getattr(settings, "NEXUS_MONITOR_DELIVERY_MAX_ATTEMPTS", 5))
                    fields.update(delivery_status="failed" if terminal else "pending", error_code="EMAIL_TRANSPORT_FAILED",
                        error_message="EMAIL_TRANSPORT_FAILED", next_attempt_at=now + timedelta(seconds=min(3600, 15 * 2 ** min(row.attempts, 8))))
                    outcome = "failed" if terminal else "retrying"
                    error = "EMAIL_TRANSPORT_FAILED"
                else:
                    fields.update(delivery_status="sent", sent_at=timezone.now(), error_code="", error_message="")
                    outcome = "sent"
            # Never hold a database transaction across SMTP. Fence the late result.
            updated = AlertNotification.objects.filter(pk=row.pk, lease_id=row.lease_id).update(**fields)
            if updated:
                result[outcome] += 1
                if outcome == "sent":
                    _audit(tenant, "metrics.alert_notification.send", "alert_notification", row,
                        transport_accepted=True, attempts=row.attempts)
    except Exception:
        error = "DELIVERY_WORKER_FAILED"
    finally:
        finish_component(tenant, "delivery", lease, error)
    return result


def cleanup_monitoring(*, dry_run=True, batch_size=500):
    batch_size = _bound(batch_size, 1000, "batch_size")
    if type(dry_run) is not bool:
        raise ValidationError({"dry_run": "Use a boolean."})
    owner, tenant, project = installation()
    now = timezone.now()
    days = max(30, int(getattr(settings, "NEXUS_MONITOR_RETENTION_DAYS", 90)))
    cutoff = now - timedelta(days=days)
    # Legacy snapshots have no Project column. Only this host's explicit
    # Project-tagged snapshots are eligible; never guess ownership of old JSON.
    candidates = {"snapshots": MetricSnapshot.objects.filter(tenant=tenant, captured_at__lt=cutoff,
            metrics_json__project_id=project),
        "notifications": _notifications(tenant, project, owner).filter(delivery_status="sent", sent_at__lt=cutoff)
            .filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now))}
    result = {"dry_run": dry_run, "retention_days": days}
    for name, rows in candidates.items():
        ids = list(rows.order_by("pk").values_list("pk", flat=True)[:batch_size])
        result[name] = len(ids)
        if not dry_run and ids:
            from django.db.models.query import QuerySet
            QuerySet.delete(rows.filter(pk__in=ids))
    return result
