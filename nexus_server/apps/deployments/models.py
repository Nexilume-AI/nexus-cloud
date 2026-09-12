from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import BaseModel, SoftDeleteModel
from apps.providers.models import Provider, ProviderAccount
from apps.tenancy.models import Project, Team, Tenant


class CanonicalModel(SoftDeleteModel):
    STATUS_ACTIVE = SoftDeleteModel.STATUS_ACTIVE
    STATUS_DISABLED = SoftDeleteModel.STATUS_DISABLED

    key = models.SlugField(max_length=128, unique=True)
    display_name = models.CharField(max_length=255)
    family = models.CharField(max_length=128, blank=True)
    modalities = models.JSONField(default=list, blank=True)
    input_modalities = models.JSONField(default=list, blank=True)
    output_modalities = models.JSONField(default=list, blank=True)
    operations = models.JSONField(default=list, blank=True)
    capabilities = models.JSONField(default=list, blank=True)
    context_window = models.PositiveIntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["key"]
        indexes = [models.Index(fields=["status", "key"])]

    def __str__(self) -> str:
        return self.key


class Deployment(SoftDeleteModel):
    HEALTH_UNKNOWN = "unknown"
    HEALTH_HEALTHY = "healthy"
    HEALTH_DEGRADED = "degraded"
    HEALTH_UNHEALTHY = "unhealthy"
    HEALTH_CHOICES = (
        (HEALTH_UNKNOWN, "Unknown"),
        (HEALTH_HEALTHY, "Healthy"),
        (HEALTH_DEGRADED, "Degraded"),
        (HEALTH_UNHEALTHY, "Unhealthy"),
    )

    VISIBILITY_PUBLIC = "public"
    VISIBILITY_PRIVATE = "private"
    VISIBILITY_TENANT = "tenant"
    VISIBILITY_TEAM = "team"
    VISIBILITY_PROJECT = "project"
    VISIBILITY_CHOICES = (
        (VISIBILITY_PUBLIC, "Public"),
        (VISIBILITY_PRIVATE, "Private"),
        (VISIBILITY_TENANT, "Tenant"),
        (VISIBILITY_TEAM, "Team"),
        (VISIBILITY_PROJECT, "Project"),
    )

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="deployments")
    provider = models.ForeignKey(Provider, on_delete=models.CASCADE, related_name="deployments")
    provider_account = models.ForeignKey(
        ProviderAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deployments",
    )
    deployment_id = models.CharField(max_length=128)
    canonical_model = models.ForeignKey(
        CanonicalModel,
        on_delete=models.PROTECT,
        related_name="sources",
    )
    upstream_model_id = models.CharField(max_length=255)
    provider_runtime = models.ForeignKey(
        "providers.ProviderRuntimeAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sources",
    )
    runtime_model_offer = models.ForeignKey(
        "providers.ProviderRuntimeModelOffer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sources",
    )
    endpoint = models.URLField(max_length=1024, blank=True)
    visibility = models.CharField(max_length=32, choices=VISIBILITY_CHOICES, default=VISIBILITY_TENANT)
    team = models.ForeignKey(Team, on_delete=models.SET_NULL, null=True, blank=True, related_name="deployments")
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name="deployments")
    pricing_rate = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    health_status = models.CharField(max_length=32, choices=HEALTH_CHOICES, default=HEALTH_UNKNOWN)
    health_reason = models.CharField(max_length=512, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_failure_at = models.DateTimeField(null=True, blank=True)
    consecutive_failures = models.PositiveIntegerField(default=0)
    last_latency_ms = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_deployments",
    )
    last_checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "deployment_id"], name="unique_tenant_deployment_id"),
        ]
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["tenant", "health_status"]),
            models.Index(fields=["tenant", "visibility", "status"]),
        ]

    def __str__(self) -> str:
        return self.deployment_id

    @property
    def model(self) -> str:
        """Internal transport alias; public APIs expose upstream_model_id explicitly."""

        return self.upstream_model_id

    @model.setter
    def model(self, value: str) -> None:
        self.upstream_model_id = value


class ModelGroup(SoftDeleteModel):
    ROUTING_FALLBACK = "fallback"
    ROUTING_BEST_HEALTH = "best_health"
    ROUTING_LOWEST_COST = "lowest_cost"
    ROUTING_LOWEST_LATENCY = "lowest_latency"
    ROUTING_WEIGHTED = "weighted"
    ROUTING_CHOICES = (
        (ROUTING_FALLBACK, "Fallback"),
        (ROUTING_BEST_HEALTH, "Best health"),
        (ROUTING_LOWEST_COST, "Lowest cost"),
        (ROUTING_LOWEST_LATENCY, "Lowest latency"),
        (ROUTING_WEIGHTED, "Weighted"),
    )

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="model_groups")
    canonical_model = models.ForeignKey(
        CanonicalModel,
        on_delete=models.PROTECT,
        related_name="model_groups",
    )
    name = models.CharField(max_length=128)
    display_name = models.CharField(max_length=255, blank=True)
    visibility = models.CharField(max_length=32, choices=Deployment.VISIBILITY_CHOICES, default=Deployment.VISIBILITY_TENANT)
    routing_strategy = models.CharField(max_length=32, choices=ROUTING_CHOICES, default=ROUTING_FALLBACK)
    routing_config = models.JSONField(default=dict, blank=True)
    routing_revision = models.PositiveBigIntegerField(default=1)
    team = models.ForeignKey(Team, on_delete=models.SET_NULL, null=True, blank=True, related_name="model_groups")
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name="model_groups")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_model_groups",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "name"], name="unique_tenant_model_group"),
        ]
        indexes = [
            models.Index(fields=["tenant", "visibility", "status"]),
        ]


class ModelGroupDeployment(SoftDeleteModel):
    model_group = models.ForeignKey(ModelGroup, on_delete=models.CASCADE, related_name="deployment_links")
    deployment = models.ForeignKey(Deployment, on_delete=models.CASCADE, related_name="model_group_links")
    enabled = models.BooleanField(default=True)
    priority = models.PositiveIntegerField(default=100)
    weight = models.PositiveIntegerField(default=100)
    fallback_order = models.PositiveIntegerField(default=100)
    last_selected_at = models.DateTimeField(null=True, blank=True)
    selection_count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["model_group", "deployment"], name="unique_model_group_deployment"),
        ]
        indexes = [
            models.Index(fields=["model_group", "status", "enabled", "priority"]),
        ]


class ModelGroupRoutingRevision(BaseModel):
    """Immutable production routing snapshot used for audit and rollback."""

    model_group = models.ForeignKey(ModelGroup, on_delete=models.CASCADE, related_name="routing_revisions")
    revision = models.PositiveBigIntegerField()
    routing_strategy = models.CharField(max_length=32, choices=ModelGroup.ROUTING_CHOICES)
    routing_config = models.JSONField(default=dict, blank=True)
    sources = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_model_group_routing_revisions",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["model_group", "revision"], name="unique_model_group_routing_revision"),
        ]
        indexes = [
            models.Index(fields=["model_group", "-revision"], name="deployments_model_g_1ed957_idx"),
        ]


class DeploymentHealthCheck(models.Model):
    STATUS_HEALTHY = Deployment.HEALTH_HEALTHY
    STATUS_DEGRADED = Deployment.HEALTH_DEGRADED
    STATUS_UNHEALTHY = Deployment.HEALTH_UNHEALTHY

    deployment = models.ForeignKey(Deployment, on_delete=models.CASCADE, related_name="health_checks")
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="deployment_health_checks")
    status = models.CharField(max_length=32, choices=Deployment.HEALTH_CHOICES)
    reason = models.CharField(max_length=512, blank=True)
    latency_ms = models.PositiveIntegerField(default=0)
    checked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "status", "checked_at"]),
            models.Index(fields=["deployment", "checked_at"]),
        ]
