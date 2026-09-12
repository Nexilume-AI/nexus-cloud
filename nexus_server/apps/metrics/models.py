from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import SoftDeleteModel
from apps.tenancy.models import Tenant, Project


class MetricSnapshot(SoftDeleteModel):
    RESOURCE_API_KEY = "api_key"
    RESOURCE_MODEL = "model"
    RESOURCE_AGENT = "agent"
    RESOURCE_DATASET = "dataset"
    RESOURCE_ROUTER = "router"
    RESOURCE_SYSTEM = "system"
    RESOURCE_CHOICES = (
        (RESOURCE_API_KEY, "API key"),
        (RESOURCE_MODEL, "Model"),
        (RESOURCE_AGENT, "Agent"),
        (RESOURCE_DATASET, "Dataset"),
        (RESOURCE_ROUTER, "Router"),
        (RESOURCE_SYSTEM, "System"),
    )

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="metric_snapshots")
    resource_type = models.CharField(max_length=64, choices=RESOURCE_CHOICES)
    resource_id = models.CharField(max_length=128, blank=True)
    metrics_json = models.JSONField(default=dict, blank=True)
    captured_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "resource_type", "resource_id", "captured_at"]),
            models.Index(fields=["tenant", "captured_at"]),
        ]


class AlertRule(SoftDeleteModel):
    OPERATOR_GT = "gt"
    OPERATOR_GTE = "gte"
    OPERATOR_LT = "lt"
    OPERATOR_LTE = "lte"
    OPERATOR_EQ = "eq"
    OPERATOR_CHOICES = (
        (OPERATOR_GT, ">"),
        (OPERATOR_GTE, ">="),
        (OPERATOR_LT, "<"),
        (OPERATOR_LTE, "<="),
        (OPERATOR_EQ, "="),
    )

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="alert_rules")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, null=True, blank=True, related_name="alert_rules")
    metric = models.CharField(max_length=128)
    threshold = models.CharField(max_length=64)
    threshold_value = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    threshold_unit = models.CharField(max_length=16, blank=True)
    operator = models.CharField(max_length=16, choices=OPERATOR_CHOICES, default=OPERATOR_GTE)
    resource_type = models.CharField(max_length=64, blank=True)
    resource_id = models.CharField(max_length=128, blank=True)
    window_seconds = models.PositiveIntegerField(default=300)
    cooldown_seconds = models.PositiveIntegerField(default=300)
    notification_channels = models.JSONField(default=dict, blank=True)
    last_evaluated_at = models.DateTimeField(null=True, blank=True)
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=64, blank=True)
    muted_until = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_alert_rules",
    )

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "metric", "status"]),
            models.Index(fields=["tenant", "resource_type", "resource_id", "status"]),
        ]


class AlertEvent(SoftDeleteModel):
    STATUS_FIRING = "firing"
    STATUS_RESOLVED = "resolved"
    EVENT_STATUS_CHOICES = (
        (STATUS_FIRING, "Firing"),
        (STATUS_RESOLVED, "Resolved"),
        (SoftDeleteModel.STATUS_DELETED, "Deleted"),
    )

    status = models.CharField(max_length=32, choices=EVENT_STATUS_CHOICES, default=STATUS_FIRING)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="alert_events")
    rule = models.ForeignKey(AlertRule, on_delete=models.CASCADE, related_name="events")
    metric = models.CharField(max_length=128)
    resource_type = models.CharField(max_length=64, blank=True)
    resource_id = models.CharField(max_length=128, blank=True)
    value = models.DecimalField(max_digits=24, decimal_places=6)
    threshold_value = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    threshold = models.CharField(max_length=64, blank=True)
    triggered_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)
    metadata_json = models.JSONField(default=dict, blank=True)
    is_test = models.BooleanField(default=False)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["rule"], condition=models.Q(status="firing", is_test=False), name="metrics_one_open_incident")]
        indexes = [
            models.Index(fields=["tenant", "status", "triggered_at"]),
            models.Index(fields=["tenant", "rule", "status"]),
        ]


class DeliveryState(models.Model):
    # Stable key survives retries. Legacy rows intentionally have no inferred key.
    delivery_key = models.CharField(max_length=128, null=True, blank=True, unique=True)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    lease_until = models.DateTimeField(null=True, blank=True)
    lease_id = models.CharField(max_length=36, blank=True)

    class Meta:
        abstract = True


class AlertNotification(DeliveryState, SoftDeleteModel):
    STATUS_PENDING = "pending"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    DELIVERY_STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_SENT, "Sent"),
        (STATUS_FAILED, "Failed"),
        (SoftDeleteModel.STATUS_DELETED, "Deleted"),
    )
    CHANNEL_EMAIL = "email"
    CHANNEL_CHOICES = ((CHANNEL_EMAIL, "Email"),)

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="alert_notifications")
    event = models.ForeignKey(AlertEvent, on_delete=models.CASCADE, related_name="notifications")
    channel = models.CharField(max_length=32, choices=CHANNEL_CHOICES, default=CHANNEL_EMAIL)
    target = models.CharField(max_length=256)
    phase = models.CharField(max_length=16, default="firing")
    delivery_status = models.CharField(max_length=32, choices=DELIVERY_STATUS_CHOICES, default=STATUS_PENDING)
    sent_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "delivery_status", "created_at"]),
            models.Index(fields=["tenant", "event", "channel"]),
            models.Index(fields=["tenant", "delivery_status", "next_attempt_at"], name="metrics_alert_outbox_due"),
        ]


class MonitoringHeartbeat(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    component = models.CharField(max_length=32)
    last_started_at = models.DateTimeField(null=True)
    last_success_at = models.DateTimeField(null=True)
    last_error_code = models.CharField(max_length=64, blank=True)
    lease_until = models.DateTimeField(null=True)
    lease_id = models.CharField(max_length=36, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "component"], name="metrics_heartbeat_component")]


def __getattr__(name):
    if name in {"ReportSchedule", "ReportDelivery"}:
        from apps.common.schema_extension import schema_extension
        return getattr(schema_extension("NEXUS_MONITORING_MODEL_EXTENSION"), name)
    raise AttributeError(name)
