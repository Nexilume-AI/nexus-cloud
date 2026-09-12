from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import BaseModel
from apps.deployments.models import Deployment, ModelGroup
from apps.routers.models import Router
from apps.tenancy.models import Tenant


class GatewayRequestLog(BaseModel):
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = (
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILED, "Failed"),
    )

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="gateway_request_logs")
    project_id = models.CharField(max_length=128, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gateway_request_logs",
    )
    model = models.CharField(max_length=255)
    operation = models.CharField(max_length=32, default="chat.completions")
    image_count = models.PositiveIntegerField(default=0)
    router = models.ForeignKey(Router, on_delete=models.SET_NULL, null=True, blank=True, related_name="gateway_request_logs")
    router_strategy = models.CharField(max_length=32, blank=True)
    deployment = models.ForeignKey(Deployment, on_delete=models.SET_NULL, null=True, blank=True, related_name="gateway_request_logs")
    consumer_source = models.ForeignKey(
        Deployment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consumer_gateway_request_logs",
    )
    selected_model_group = models.ForeignKey(
        ModelGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gateway_request_logs",
    )
    routing_trace = models.JSONField(default=dict, blank=True)
    provider = models.CharField(max_length=64, blank=True)
    request_tokens = models.PositiveIntegerField(default=0)
    response_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES)
    error_code = models.CharField(max_length=64, blank=True)
    fallback_count = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    request_id = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "created_at"]),
            models.Index(fields=["tenant", "model", "created_at"]),
            models.Index(fields=["tenant", "router", "created_at"]),
            models.Index(fields=["tenant", "status", "created_at"]),
        ]


class GatewayImageOperation(BaseModel):
    """Durable deduplication; never persist prompts, image bytes or signed URLs."""
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    principal = models.CharField(max_length=80)
    key_digest = models.CharField(max_length=64)
    request_digest = models.CharField(max_length=64)
    state = models.CharField(max_length=16, default="pending")
    operation = models.CharField(max_length=32)
    asset_ids = models.JSONField(default=list)
    error_code = models.CharField(max_length=64, blank=True)
    gateway_log = models.ForeignKey(GatewayRequestLog, null=True, on_delete=models.SET_NULL)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "principal", "key_digest"], name="gateway_image_idempotency")]
