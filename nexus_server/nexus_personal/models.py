from django.conf import settings
from django.db import models
import uuid


class PersonalInstallation(models.Model):
    """Exactly one locally provisioned owner; never exposed as a mutable API."""
    slot = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    tenant = models.OneToOneField("tenancy.Tenant", on_delete=models.PROTECT)
    project = models.OneToOneField("tenancy.Project", on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(slot=1), name="personal_single_installation")]


class PersonalDataExportUsage(models.Model):
    """Exact bytes from a completed, authorized transfer, not a financial ledger."""
    transfer = models.OneToOneField("datasets.DatasetTransfer", on_delete=models.PROTECT)
    idempotency_key = models.CharField(max_length=128, unique=True)
    size_bytes = models.PositiveBigIntegerField()
    recorded_at = models.DateTimeField(auto_now_add=True, db_index=True)


class PersonalRunReservation(models.Model):
    """Short-lived pre-Run admission, never a wallet or commercial entitlement."""
    run_id = models.UUIDField(primary_key=True)
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE)
    expires_at = models.DateTimeField(db_index=True)
    released_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class PersonalInvocationUsage(models.Model):
    """Nonfinancial execution receipt retained if a Run/Agent is deleted."""
    invocation = models.OneToOneField("agents.AgentRuntimeInvocation", null=True,
        on_delete=models.SET_NULL)
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.PROTECT)
    run_id = models.UUIDField()
    turn_index = models.PositiveIntegerField()
    started_at = models.DateTimeField(db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    latency_ms = models.PositiveBigIntegerField(default=0)
    reserved_ms = models.PositiveBigIntegerField()
    # Deliberately not SET_NULL: deleting a credential must not turn a queued
    # capability-scoped invocation into a full-owner invocation.
    agent_credential_id = models.UUIDField(null=True, blank=True, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["run_id", "turn_index"],
            name="personal_invocation_usage_turn")]


class PersonalRouterCredential(models.Model):
    """Owner-issued Router-only capability, not a general identity or IAM key."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE)
    project = models.ForeignKey("tenancy.Project", on_delete=models.CASCADE)
    router = models.ForeignKey("routers.Router", on_delete=models.CASCADE, related_name="personal_credentials")
    token_hash = models.CharField(max_length=64, unique=True)
    model_names = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    issued_for = models.CharField(max_length=16, default="exported", choices=[("exported", "Exported"), ("tool_setup", "Tool Setup")])
    # Immutable identity snapshots, deliberately not SET_NULL relations: deleting
    # a device/profile must never turn its key into an unrestricted export key.
    tool_connection_id = models.UUIDField(null=True, blank=True, db_index=True)
    tool_device_id = models.UUIDField(null=True, blank=True, db_index=True)
    tool_profile_id = models.UUIDField(null=True, blank=True, db_index=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=(
            models.Q(issued_for="exported", tool_connection_id__isnull=True, tool_device_id__isnull=True, tool_profile_id__isnull=True) |
            models.Q(issued_for="tool_setup", tool_connection_id__isnull=False, tool_device_id__isnull=False, tool_profile_id__isnull=False)
        ), name="personal_router_key_origin_valid")]


class PersonalAgentCredential(models.Model):
    """Computer-bound Agent MCP capability; never an IAM or owner credential."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=models.CASCADE)
    project = models.ForeignKey('tenancy.Project', on_delete=models.CASCADE)
    token_hash = models.CharField(max_length=64, unique=True)
    agent_ids = models.JSONField(default=list)
    tool_connection_id = models.UUIDField(db_index=True)
    tool_device_id = models.UUIDField(db_index=True)
    tool_profile_id = models.UUIDField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)


class PersonalToolConfigOperation(models.Model):
    """Durable write ownership; prior configuration and remote command payloads are encrypted."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.ForeignKey("workspaces.WorkspaceToolManagedProfile", on_delete=models.CASCADE,
        related_name="personal_operations")
    request_key = models.CharField(max_length=64)
    request_digest = models.CharField(max_length=64)
    state = models.CharField(max_length=24, default="writing")
    active = models.BooleanField(default=True)
    action = models.CharField(max_length=32)
    section = models.CharField(max_length=16, default="api")
    # UUID snapshots survive key deletion: a missing key is never owner access.
    agent_credential_id = models.UUIDField(null=True, blank=True)
    old_agent_credential_id = models.UUIDField(null=True, blank=True)
    previous_managed_mcp = models.JSONField(default=dict)
    target_managed_mcp = models.JSONField(default=dict)
    expected_revision = models.CharField(max_length=64)
    target_revision = models.CharField(max_length=64)
    profile_revision = models.CharField(max_length=64, blank=True)
    old_credential = models.ForeignKey(PersonalRouterCredential, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="replaced_by_tool_operations")
    credential = models.ForeignKey(PersonalRouterCredential, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="tool_operations")
    credential_created = models.BooleanField(default=False)
    command = models.OneToOneField("workspaces.ComputerRuntimeCommand", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="personal_tool_operation")
    error_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    encrypted_before_config = models.TextField(blank=True)
    fence_command = models.ForeignKey("workspaces.ComputerRuntimeCommand", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="personal_tool_fences")
    restore_command = models.ForeignKey("workspaces.ComputerRuntimeCommand", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="personal_tool_restores")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["profile", "request_key"], name="personal_tool_request_unique"),
            models.UniqueConstraint(fields=["profile"], condition=models.Q(active=True), name="personal_tool_active_unique"),
        ]


class PersonalGatewayRequest(models.Model):
    """Durable nonfinancial replay fence; never stores prompts or credentials."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.PROTECT)
    project = models.ForeignKey("tenancy.Project", on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    request_id = models.CharField(max_length=64)
    request_digest = models.CharField(max_length=64)
    requested_model = models.CharField(max_length=255)
    operation = models.CharField(max_length=32, default="chat.completions")
    router = models.ForeignKey("routers.Router", null=True, blank=True, on_delete=models.SET_NULL)
    source_ids = models.JSONField(default=list)
    state = models.CharField(max_length=16, default="pending")
    started_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    result_log = models.OneToOneField("gateway.GatewayRequestLog", null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "request_id"], name="personal_gateway_request_unique")]


class PersonalGatewayAttempt(models.Model):
    """One dispatch to a Source, including interrupted or incomplete streams."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(PersonalGatewayRequest, on_delete=models.CASCADE, related_name="attempts")
    source_id = models.UUIDField()
    state = models.CharField(max_length=16, default="dispatched")
    total_tokens = models.PositiveBigIntegerField(default=0)
    error_code = models.CharField(max_length=64, blank=True)
    dispatched_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["request", "source_id"], name="personal_gateway_attempt_unique")]
