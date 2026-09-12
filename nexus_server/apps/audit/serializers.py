from __future__ import annotations

from rest_framework import serializers

from .models import AuditLog
from .services import canonical_action


class AuditLogSerializer(serializers.ModelSerializer):
    action = serializers.SerializerMethodField()
    actor_id = serializers.SerializerMethodField()
    actor_email = serializers.EmailField(source="actor.email", read_only=True, allow_null=True)

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "tenant_id",
            "project_id",
            "actor_id",
            "actor_email",
            "action",
            "resource_type",
            "resource_id",
            "before_snapshot",
            "after_snapshot",
            "ip_address",
            "user_agent",
            "request_id",
            "metadata",
            "created_at",
            "updated_at",
        ]

    def get_action(self, obj: AuditLog) -> str:
        return canonical_action(obj.action)

    def get_actor_id(self, obj: AuditLog) -> str:
        return str(obj.actor_id) if obj.actor_id else ""
