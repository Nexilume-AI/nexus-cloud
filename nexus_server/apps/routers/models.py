from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import BaseModel, SoftDeleteModel
from apps.tenancy.models import Project, Tenant


class Router(SoftDeleteModel):
    TYPE_EXECUTION = "execution"
    TYPE_AGGREGATION = "aggregation"
    TYPE_CHOICES = (
        (TYPE_EXECUTION, "Execution Router"),
        (TYPE_AGGREGATION, "Aggregation Router"),
    )

    STRATEGY_COST = "cost"
    STRATEGY_QUALITY = "quality"
    STRATEGY_CUSTOM = "custom"
    STRATEGY_MANUAL_PRIORITY = "manual_priority"
    STRATEGY_LOWEST_COST_POOL = "lowest_cost_pool"
    STRATEGY_LOWEST_LATENCY_POOL = "lowest_latency_pool"
    STRATEGY_BEST_HEALTH_POOL = "best_health_pool"
    STRATEGY_TASK_TYPE = "task_type"
    STRATEGY_CHOICES = (
        (STRATEGY_COST, "Cost"),
        (STRATEGY_QUALITY, "Quality"),
        (STRATEGY_CUSTOM, "Custom"),
        (STRATEGY_MANUAL_PRIORITY, "Manual priority"),
        (STRATEGY_LOWEST_COST_POOL, "Lowest cost pool"),
        (STRATEGY_LOWEST_LATENCY_POOL, "Lowest latency pool"),
        (STRATEGY_BEST_HEALTH_POOL, "Best health pool"),
        (STRATEGY_TASK_TYPE, "Task type"),
    )

    STATUS_DRAFT = "draft"
    STATUS_DEPLOYED = "deployed"
    STATUS_DISABLED = "disabled"
    STATUS_CHOICES = (
        (STATUS_DRAFT, "Draft"),
        (STATUS_DEPLOYED, "Deployed"),
        (STATUS_DISABLED, "Disabled"),
        (SoftDeleteModel.STATUS_DELETED, "Deleted"),
    )

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="routers")
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name="routers")
    name = models.CharField(max_length=255)
    router_type = models.CharField(max_length=32, choices=TYPE_CHOICES, default=TYPE_EXECUTION)
    strategy = models.CharField(max_length=32, choices=STRATEGY_CHOICES, default=STRATEGY_COST)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    current_version = models.CharField(max_length=32, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_routers",
    )

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["tenant", "created_at"]),
            models.Index(fields=["tenant", "project", "status"], name="routers_t_proj_status_idx"),
        ]

    def __str__(self) -> str:
        return self.name


class RouterVersion(SoftDeleteModel):
    STATUS_UPLOADED = "uploaded"
    STATUS_DEPLOYED = "deployed"
    STATUS_CHOICES = (
        (STATUS_UPLOADED, "Uploaded"),
        (STATUS_DEPLOYED, "Deployed"),
    )

    router = models.ForeignKey(Router, on_delete=models.CASCADE, related_name="versions")
    version = models.CharField(max_length=32)
    router_file_path = models.CharField(max_length=1024)
    file_name = models.CharField(max_length=255, blank=True)
    file_size = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_UPLOADED)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_router_versions",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["router", "version"], name="unique_router_version"),
        ]
        indexes = [
            models.Index(fields=["router", "status", "created_at"]),
        ]


class RouterModelGroupBinding(SoftDeleteModel):
    router = models.ForeignKey(Router, on_delete=models.CASCADE, related_name="model_group_bindings")
    model_group = models.ForeignKey(
        "deployments.ModelGroup",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="router_bindings",
    )
    provider_name = models.CharField(max_length=128)
    model_group_name = models.CharField(max_length=128)
    enabled = models.BooleanField(default=True)
    priority = models.PositiveIntegerField(default=100)
    weight = models.PositiveIntegerField(default=100)
    routing_hint = models.CharField(max_length=128, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["router", "provider_name", "model_group_name"],
                name="unique_router_model_group_binding",
            ),
        ]
        indexes = [
            models.Index(fields=["router", "status", "enabled", "priority"]),
        ]


class RouterOutput(SoftDeleteModel):
    """A stable model name exported by an execution Router."""

    router = models.ForeignKey(Router, on_delete=models.CASCADE, related_name="outputs")
    model_name = models.CharField(max_length=128)
    model_group = models.ForeignKey(
        "deployments.ModelGroup",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="router_outputs",
    )
    description = models.CharField(max_length=512, blank=True)
    enabled = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["router", "model_name"], name="unique_router_output_model_name"),
        ]
        indexes = [
            models.Index(fields=["router", "status", "enabled", "created_at"]),
        ]


class RouterChildBinding(SoftDeleteModel):
    """Maps one Aggregation Router model name to an execution Router output."""

    router = models.ForeignKey(Router, on_delete=models.CASCADE, related_name="child_bindings")
    exposed_model_name = models.CharField(max_length=128)
    child_output = models.ForeignKey(RouterOutput, on_delete=models.CASCADE, related_name="parent_bindings")
    enabled = models.BooleanField(default=True)
    priority = models.PositiveIntegerField(default=100)
    weight = models.PositiveIntegerField(default=100)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["router", "exposed_model_name", "child_output"],
                name="unique_router_child_output_binding",
            ),
        ]
        indexes = [
            models.Index(fields=["router", "status", "enabled", "exposed_model_name", "priority"]),
        ]


class RouterProviderPreference(SoftDeleteModel):
    TYPE_OWN_FIRST = "own_first"
    TYPE_POOL_FIRST = "pool_first"
    TYPE_SPECIFIC_PROVIDER = "specific_provider"
    TYPE_CHOICES = (
        (TYPE_OWN_FIRST, "Own First"),
        (TYPE_POOL_FIRST, "Pool First"),
        (TYPE_SPECIFIC_PROVIDER, "Specific Provider"),
    )

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="router_provider_preferences")
    router = models.ForeignKey(Router, on_delete=models.CASCADE, related_name="provider_preferences")
    provider_account = models.ForeignKey(
        "providers.ProviderAccount",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="router_preferences",
    )
    preference_type = models.CharField(max_length=32, choices=TYPE_CHOICES, default=TYPE_OWN_FIRST)
    priority = models.PositiveIntegerField(default=100)
    weight = models.PositiveIntegerField(default=100)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "router", "status", "priority"]),
        ]




class RouterDeployment(SoftDeleteModel):
    STATUS_DEPLOYING = "deploying"
    STATUS_ACTIVE = "active"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = (
        (STATUS_DEPLOYING, "Deploying"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_FAILED, "Failed"),
    )

    router = models.ForeignKey(Router, on_delete=models.CASCADE, related_name="deployments")
    version = models.ForeignKey(
        RouterVersion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deployments",
    )
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_DEPLOYING)
    endpoint_url = models.CharField(max_length=1024, blank=True)
    deployed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="router_deployments",
    )

    class Meta:
        indexes = [
            models.Index(fields=["router", "status", "created_at"]),
        ]


class RouterRuntimeInvocation(BaseModel):
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = (
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILED, "Failed"),
    )

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="router_runtime_invocations")
    router = models.ForeignKey(Router, on_delete=models.CASCADE, related_name="runtime_invocations")
    version = models.ForeignKey(
        RouterVersion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runtime_invocations",
    )
    selected_deployment = models.ForeignKey(
        "deployments.Deployment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="router_runtime_invocations",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="router_runtime_invocations",
    )
    status = models.CharField(max_length=32, choices=STATUS_CHOICES)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.CharField(max_length=512, blank=True)
    latency_ms = models.PositiveIntegerField(default=0)
    exit_code = models.IntegerField(null=True, blank=True)
    request_id = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "router", "created_at"]),
            models.Index(fields=["tenant", "status", "created_at"]),
            models.Index(fields=["tenant", "request_id"]),
        ]


def __getattr__(name):
    if name == "RouterPricing":
        from apps.common.schema_extension import schema_extension
        return getattr(schema_extension("NEXUS_ROUTER_MODEL_EXTENSION"), name)
    raise AttributeError(name)
