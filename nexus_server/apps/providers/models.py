from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import BaseModel, SoftDeleteModel
from apps.tenancy.models import Project, Tenant


class Provider(SoftDeleteModel):
    name = models.SlugField(max_length=64, unique=True)
    display_name = models.CharField(max_length=255, blank=True)

    def __str__(self) -> str:
        return self.name


class ProviderImportBatch(BaseModel):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="provider_imports")
    owner_subject_hash = models.CharField(max_length=64)
    context_project_id = models.CharField(max_length=36, blank=True)
    request_key = models.UUIDField()
    content_hash = models.CharField(max_length=64)
    duplicate_mode = models.CharField(max_length=8, default="skip")
    encrypted_payload = models.TextField(blank=True)
    results = models.JSONField(default=list)
    expires_at = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=16, default="preview")

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=["tenant", "owner_subject_hash", "request_key"], name="provider_import_request_unique")]


class ProviderAccount(SoftDeleteModel):
    AUTH_API_KEY = "api_key"
    AUTH_INTERACTIVE_LOGIN = "interactive_login"
    AUTH_USERNAME_PASSWORD_LOGIN = "username_password_login"
    AUTH_CHOICES = (
        (AUTH_API_KEY, "API Key"),
        (AUTH_INTERACTIVE_LOGIN, "Interactive Login"),
        (AUTH_USERNAME_PASSWORD_LOGIN, "Username Password Login"),
    )
    PREFERRED_RUNTIME_CODEX_PROXY = "codex_proxy"
    PREFERRED_RUNTIME_CLIPROXYAPI = "cliproxyapi"
    PREFERRED_RUNTIME_CHOICES = (
        (PREFERRED_RUNTIME_CODEX_PROXY, "Codex Proxy"),
        (PREFERRED_RUNTIME_CLIPROXYAPI, "CLIProxyAPI"),
    )

    LOGIN_UNKNOWN = "unknown"
    LOGIN_REQUIRED = "login_required"
    LOGIN_LOGGING_IN = "logging_in"
    LOGIN_ACTIVE = "active"
    LOGIN_FAILED = "failed"
    LOGIN_CHOICES = (
        (LOGIN_UNKNOWN, "Unknown"),
        (LOGIN_REQUIRED, "Login Required"),
        (LOGIN_LOGGING_IN, "Logging In"),
        (LOGIN_ACTIVE, "Active"),
        (LOGIN_FAILED, "Failed"),
    )

    QUOTA_UNKNOWN = "unknown"
    QUOTA_AVAILABLE = "available"
    QUOTA_LIMITED = "limited"
    QUOTA_EXHAUSTED = "exhausted"
    QUOTA_CHOICES = (
        (QUOTA_UNKNOWN, "Unknown"),
        (QUOTA_AVAILABLE, "Available"),
        (QUOTA_LIMITED, "Limited"),
        (QUOTA_EXHAUSTED, "Exhausted"),
    )

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="provider_accounts")
    provider = models.ForeignKey(Provider, on_delete=models.CASCADE, related_name="accounts")
    name = models.CharField(max_length=255, blank=True)
    account_id = models.CharField(max_length=128)
    url = models.URLField(max_length=1024, blank=True)
    encrypted_key = models.TextField(blank=True)
    encrypted_username = models.TextField(blank=True)
    encrypted_password = models.TextField(blank=True)
    auth_mode = models.CharField(max_length=32, choices=AUTH_CHOICES, default=AUTH_API_KEY)
    preferred_runtime_type = models.CharField(max_length=32, choices=PREFERRED_RUNTIME_CHOICES, blank=True)
    login_status = models.CharField(max_length=32, choices=LOGIN_CHOICES, default=LOGIN_UNKNOWN)
    last_login_error = models.CharField(max_length=512, blank=True)
    last_login_at = models.DateTimeField(null=True, blank=True)
    pricing_rate = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_provider_accounts",
    )
    last_checked_at = models.DateTimeField(null=True, blank=True)
    quota_status = models.CharField(max_length=32, choices=QUOTA_CHOICES, default=QUOTA_UNKNOWN)
    quota_reset_at = models.DateTimeField(null=True, blank=True)
    quota_remaining_tokens = models.PositiveBigIntegerField(null=True, blank=True)
    quota_remaining_requests = models.PositiveBigIntegerField(null=True, blank=True)
    last_quota_error = models.CharField(max_length=512, blank=True)
    last_quota_checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "provider", "account_id"], name="unique_provider_account"),
        ]
        indexes = [
            models.Index(fields=["tenant", "provider", "status"]),
            models.Index(fields=["tenant", "account_id", "status"]),
            models.Index(fields=["tenant", "quota_status", "quota_reset_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.provider.name}:{self.account_id}"


class ProviderRuntimeAccount(SoftDeleteModel):
    RUNTIME_DIRECT_API = "direct_api"
    RUNTIME_CODEX_PROXY = "codex_proxy"
    RUNTIME_CLIPROXYAPI = "cliproxyapi"
    RUNTIME_CHOICES = (
        (RUNTIME_DIRECT_API, "Direct API"),
        (RUNTIME_CODEX_PROXY, "Codex Proxy"),
        (RUNTIME_CLIPROXYAPI, "CLIProxyAPI"),
    )

    STATUS_CREATED = "created"
    STATUS_STARTING = "starting"
    STATUS_STOPPING = "stopping"
    STATUS_LOGIN_REQUIRED = "login_required"
    STATUS_ACTIVE = "active"
    STATUS_UNHEALTHY = "unhealthy"
    STATUS_STOPPED = "stopped"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = (
        (STATUS_CREATED, "Created"),
        (STATUS_STARTING, "Starting"),
        (STATUS_STOPPING, "Stopping"),
        (STATUS_LOGIN_REQUIRED, "Login Required"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_UNHEALTHY, "Unhealthy"),
        (STATUS_STOPPED, "Stopped"),
        (STATUS_FAILED, "Failed"),
        (SoftDeleteModel.STATUS_DELETED, "Deleted"),
    )
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_CREATED)

    SHARE_PRIVATE = "private"
    SHARE_SHARED_POOL = "shared_pool"
    SHARE_CHOICES = (
        (SHARE_PRIVATE, "Private"),
        (SHARE_SHARED_POOL, "Shared Pool"),
    )

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="provider_runtime_accounts")
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name="provider_runtime_accounts")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="provider_runtime_accounts",
    )
    provider_account = models.ForeignKey(
        ProviderAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runtime_accounts",
    )
    source_provider_account = models.ForeignKey(
        ProviderAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_runtime_accounts",
    )
    name = models.CharField(max_length=255)
    runtime_type = models.CharField(max_length=32, choices=RUNTIME_CHOICES)
    share_mode = models.CharField(max_length=32, choices=SHARE_CHOICES, default=SHARE_PRIVATE)
    container_id = models.CharField(max_length=255, blank=True)
    internal_login_url = models.URLField(max_length=1024, blank=True)
    internal_api_url = models.URLField(max_length=1024, blank=True)
    public_login_path = models.CharField(max_length=1024, blank=True)
    encrypted_proxy_api_key = models.TextField(blank=True)
    storage_path = models.CharField(max_length=1024, blank=True)
    last_error = models.CharField(max_length=1024, blank=True)
    last_health_check_at = models.DateTimeField(null=True, blank=True)
    quota_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source_provider_account"],
                condition=models.Q(source_provider_account__isnull=False) & ~models.Q(status=SoftDeleteModel.STATUS_DELETED),
                name="unique_active_runtime_per_provider_account",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "runtime_type", "status"]),
            models.Index(fields=["tenant", "share_mode", "status"]),
        ]

    @property
    def primary_model_offer(self):
        return self.model_offers.exclude(status=SoftDeleteModel.STATUS_DELETED).order_by(
            models.Case(
                models.When(status=ProviderRuntimeModelOffer.STATUS_CONFIRMED, then=0),
                default=1,
            ),
            "created_at",
        ).first()


class ProviderRuntimeModelOffer(SoftDeleteModel):
    image_pricing = models.JSONField(default=list, blank=True)
    STATUS_DETECTED = "detected"
    STATUS_CONFIRMED = "confirmed"
    STATUS_UNAVAILABLE = "unavailable"
    STATUS_DISABLED = SoftDeleteModel.STATUS_DISABLED
    STATUS_CHOICES = (
        (STATUS_DETECTED, "Detected"),
        (STATUS_CONFIRMED, "Confirmed"),
        (STATUS_UNAVAILABLE, "Unavailable"),
        (STATUS_DISABLED, "Disabled"),
        (SoftDeleteModel.STATUS_DELETED, "Deleted"),
    )

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

    runtime_account = models.ForeignKey(
        ProviderRuntimeAccount,
        on_delete=models.CASCADE,
        related_name="model_offers",
    )
    canonical_model = models.ForeignKey(
        "deployments.CanonicalModel",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="provider_offers",
    )
    upstream_model_id = models.CharField(max_length=255)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_DETECTED)
    price_per_1k_tokens = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    input_price_per_1k_tokens = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    output_price_per_1k_tokens = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    cached_input_price_per_1k_tokens = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    reasoning_output_price_per_1k_tokens = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    daily_limit = models.PositiveIntegerField(default=0)
    monthly_limit = models.PositiveIntegerField(default=0)
    capacity = models.PositiveIntegerField(default=0)
    capabilities = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    health_status = models.CharField(max_length=32, choices=HEALTH_CHOICES, default=HEALTH_UNKNOWN)
    health_reason = models.CharField(max_length=512, blank=True)
    last_discovered_at = models.DateTimeField(null=True, blank=True)
    last_health_check_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="confirmed_provider_model_offers",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["runtime_account", "upstream_model_id"],
                name="unique_runtime_upstream_model",
            ),
            models.UniqueConstraint(
                fields=["runtime_account", "canonical_model"],
                condition=models.Q(
                    canonical_model__isnull=False,
                    status__in=["confirmed", "unavailable"],
                ),
                name="unique_runtime_canonical_model",
            ),
        ]
        indexes = [
            models.Index(fields=["runtime_account", "status"]),
            models.Index(fields=["canonical_model", "status"]),
            models.Index(fields=["health_status", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.runtime_account_id}:{self.upstream_model_id}"










class ProviderRuntimeHealthCheck(models.Model):
    runtime_account = models.ForeignKey(
        ProviderRuntimeAccount,
        on_delete=models.CASCADE,
        related_name="health_checks",
    )
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="provider_runtime_health_checks")
    status = models.CharField(max_length=32, choices=ProviderRuntimeAccount.STATUS_CHOICES)
    reason = models.CharField(max_length=1024, blank=True)
    latency_ms = models.PositiveIntegerField(default=0)
    checked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "status", "checked_at"]),
            models.Index(fields=["runtime_account", "checked_at"]),
        ]




def __getattr__(name):
    if name in {
        "ProviderPoolContribution", "ProviderPoolUsage", "ProviderCapacityReservation",
        "ProviderUsageAccrual", "ProviderMarketplaceStats",
    }:
        from apps.common.schema_extension import schema_extension
        return getattr(schema_extension("NEXUS_PROVIDER_MODEL_EXTENSION"), name)
    raise AttributeError(name)
