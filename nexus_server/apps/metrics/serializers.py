from __future__ import annotations

from rest_framework import serializers

from .models import AlertEvent, AlertNotification, AlertRule, MetricSnapshot


class MetricSnapshotSerializer(serializers.ModelSerializer):
    tenant_id = serializers.UUIDField(source="tenant.id", read_only=True)

    class Meta:
        model = MetricSnapshot
        fields = [
            "id",
            "tenant_id",
            "resource_type",
            "resource_id",
            "metrics_json",
            "captured_at",
            "created_at",
            "updated_at",
        ]


class SafeMonitoringSerializer(serializers.ModelSerializer):
    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        if request:
            from .policy import financial, redact_financial
            from apps.tenancy.services import get_tenant_from_request
            if "monitoring_financial" not in self.context:
                self.context["monitoring_financial"] = financial(request, get_tenant_from_request(request))
            if not self.context["monitoring_financial"]:
                data = redact_financial(data)
        return data


class AlertRuleSerializer(SafeMonitoringSerializer):
    tenant_id = serializers.UUIDField(source="tenant.id", read_only=True)

    class Meta:
        model = AlertRule
        fields = [
            "id",
            "tenant_id",
            "metric",
            "threshold",
            "threshold_value",
            "threshold_unit",
            "operator",
            "resource_type",
            "resource_id",
            "window_seconds",
            "cooldown_seconds",
            "notification_channels",
            "last_evaluated_at",
            "last_triggered_at",
            "last_error_code",
            "muted_until",
            "project_id",
            "status",
            "created_at",
            "updated_at",
        ]


class AlertRuleCreateSerializer(serializers.Serializer):
    metric = serializers.CharField(max_length=128)
    threshold = serializers.CharField(max_length=64)
    operator = serializers.ChoiceField(choices=AlertRule.OPERATOR_CHOICES, required=False, default=AlertRule.OPERATOR_GTE)
    resource_type = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    resource_id = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    window_seconds = serializers.IntegerField(required=False, min_value=1, default=300)
    cooldown_seconds = serializers.IntegerField(required=False, min_value=0, default=300)
    notification_channels = serializers.JSONField(required=False, default=dict)


class AlertNotificationSerializer(SafeMonitoringSerializer):
    tenant_id = serializers.UUIDField(source="tenant.id", read_only=True)

    class Meta:
        model = AlertNotification
        fields = [
            "id",
            "tenant_id",
            "event",
            "channel",
            "target",
            "delivery_status",
            "sent_at",
            "error_code",
            "error_message",
            "phase",
            "attempts",
            "next_attempt_at",
            "created_at",
            "updated_at",
        ]


class AlertEventSerializer(SafeMonitoringSerializer):
    tenant_id = serializers.UUIDField(source="tenant.id", read_only=True)
    notifications = AlertNotificationSerializer(many=True, read_only=True)

    class Meta:
        model = AlertEvent
        fields = [
            "id",
            "tenant_id",
            "rule",
            "metric",
            "resource_type",
            "resource_id",
            "value",
            "threshold_value",
            "threshold",
            "status",
            "triggered_at",
            "resolved_at",
            "metadata_json",
            "notifications",
            "is_test",
            "acknowledged_at",
            "created_at",
            "updated_at",
        ]


def __getattr__(name):
    if name in {"ReportScheduleSerializer", "ReportScheduleCreateSerializer", "ReportDeliverySerializer"}:
        from .report_serializers_host import report_serializers
        return getattr(report_serializers(), name)
    raise AttributeError(name)
