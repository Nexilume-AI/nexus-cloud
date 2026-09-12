from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import BaseModel


class AuditLog(BaseModel):
    tenant_id = models.CharField(max_length=64, blank=True, db_index=True)
    project_id = models.CharField(max_length=64, blank=True, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=128, db_index=True)
    resource_type = models.CharField(max_length=128, blank=True)
    resource_id = models.CharField(max_length=128, blank=True)
    before_snapshot = models.JSONField(default=dict, blank=True)
    after_snapshot = models.JSONField(default=dict, blank=True)
    request_id = models.CharField(max_length=64, blank=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant_id", "action", "created_at"]),
            models.Index(fields=["tenant_id", "actor", "created_at"]),
            models.Index(fields=["tenant_id", "resource_type", "resource_id"]),
        ]
