from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import SoftDeleteModel
from apps.common.models import BaseModel
from apps.tenancy.models import Project, Team, Tenant


def agent_display_asset_upload_to(instance, filename: str) -> str:
    suffix = str(filename or "frame.bin").rsplit("/", 1)[-1]
    return f"agent-display/{instance.run_id}/{instance.id}/{suffix}"


class AgentFileTransfer(BaseModel):
    """Private, resumable file transfer; bytes live in the shared storage backend."""
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    project = models.ForeignKey(Project, null=True, on_delete=models.SET_NULL)
    agent = models.ForeignKey("Agent", on_delete=models.CASCADE)
    run = models.ForeignKey("AgentDisplayRun", null=True, on_delete=models.CASCADE, related_name="file_transfers")
    turn_index = models.PositiveIntegerField(null=True, blank=True)
    caller_subject_hash = models.CharField(max_length=64)
    direction = models.CharField(max_length=8)  # input / output
    name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=255, default="application/octet-stream")
    size_bytes = models.PositiveBigIntegerField()
    received_bytes = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    expected_sha256 = models.CharField(max_length=64, blank=True)
    state = models.CharField(max_length=16, default="uploading")
    storage_backend = models.CharField(max_length=32)
    object_key = models.TextField(blank=True)
    computer_revision = models.PositiveIntegerField(default=0)
    lease_id = models.UUIDField(null=True)
    lease_expires_at = models.DateTimeField(null=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=64, blank=True)
    source_kind = models.CharField(max_length=24, default="upload")
    source_label = models.CharField(max_length=512, blank=True)
    idempotency_key = models.CharField(max_length=128, blank=True)
    artifact = models.OneToOneField("AgentOutputArtifact", null=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=["tenant", "caller_subject_hash", "state"]), models.Index(fields=["state", "expires_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="agent_run_file_idempotency",
            )
        ]


class AgentFilePart(BaseModel):
    transfer = models.ForeignKey(AgentFileTransfer, on_delete=models.CASCADE, related_name="parts")
    # Negative indices reserve worker assembly objects, retained for crash cleanup.
    index = models.IntegerField()
    object_key = models.TextField()
    size_bytes = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["transfer", "index"], name="unique_agent_file_part")]


class Agent(SoftDeleteModel):
    STATUS_DRAFT = "draft"
    STATUS_ACTIVE = "active"
    STATUS_DISABLED = "disabled"
    STATUS_ARCHIVED = "archived"
    STATUS_CHOICES = (
        (STATUS_DRAFT, "Draft"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_DISABLED, "Disabled"),
        (STATUS_ARCHIVED, "Archived"),
        (SoftDeleteModel.STATUS_DELETED, "Deleted"),
    )

    PUBLICATION_UNPUBLISHED = "unpublished"
    PUBLICATION_PUBLISHED = "published"
    PUBLICATION_SUSPENDED = "suspended"
    PUBLICATION_STATUS_CHOICES = (
        (PUBLICATION_UNPUBLISHED, "Unpublished"),
        (PUBLICATION_PUBLISHED, "Published"),
        (PUBLICATION_SUSPENDED, "Suspended"),
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

    COMPUTER_REQUIRED = "required"
    COMPUTER_OPTIONAL = "optional"
    COMPUTER_DISABLED = "disabled"
    COMPUTER_REQUIREMENT_CHOICES = (
        (COMPUTER_REQUIRED, "Required"),
        (COMPUTER_OPTIONAL, "Optional"),
        (COMPUTER_DISABLED, "Disabled"),
    )

    MOBILE_REQUIRED = "required"
    MOBILE_OPTIONAL = "optional"
    MOBILE_DISABLED = "disabled"
    MOBILE_REQUIREMENT_CHOICES = (
        (MOBILE_REQUIRED, "Required"),
        (MOBILE_OPTIONAL, "Optional"),
        (MOBILE_DISABLED, "Disabled"),
    )

    MOBILE_POLICY_SDK = "sdk"
    MOBILE_POLICY_CLOUD = "cloud"
    MOBILE_POLICY_SOURCE_CHOICES = (
        (MOBILE_POLICY_SDK, "SDK defaults"),
        (MOBILE_POLICY_CLOUD, "Cloud override"),
    )

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="agents")
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name="agents")
    team = models.ForeignKey(Team, on_delete=models.SET_NULL, null=True, blank=True, related_name="agents")
    name = models.CharField(max_length=255)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    visibility = models.CharField(max_length=32, choices=VISIBILITY_CHOICES, default=VISIBILITY_PRIVATE)
    publication_status = models.CharField(
        max_length=32,
        choices=PUBLICATION_STATUS_CHOICES,
        default=PUBLICATION_UNPUBLISHED,
    )
    computer_requirement = models.CharField(
        max_length=16,
        choices=COMPUTER_REQUIREMENT_CHOICES,
        default=COMPUTER_OPTIONAL,
    )
    workspace_capabilities = models.JSONField(default=list, blank=True)
    mobile_requirement = models.CharField(
        max_length=16,
        choices=MOBILE_REQUIREMENT_CHOICES,
        default=MOBILE_DISABLED,
    )
    mobile_capabilities = models.JSONField(default=list, blank=True)
    mobile_policy_source = models.CharField(
        max_length=16,
        choices=MOBILE_POLICY_SOURCE_CHOICES,
        default=MOBILE_POLICY_CLOUD,
    )
    repo_metadata = models.JSONField(default=dict, blank=True)
    current_version = models.CharField(max_length=32, blank=True)
    current_image = models.ForeignKey(
        "AgentRuntimeImage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_agents",
    )

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["tenant", "visibility", "status"]),
            models.Index(fields=["tenant", "publication_status", "status"]),
        ]

    def __str__(self) -> str:
        return self.name


class AgentVersion(SoftDeleteModel):
    STATUS_PUBLISHED = "published"
    STATUS_ROLLED_BACK = "rolled_back"
    STATUS_CHOICES = (
        (STATUS_PUBLISHED, "Published"),
        (STATUS_ROLLED_BACK, "Rolled back"),
    )

    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="versions")
    version = models.CharField(max_length=32)
    commit_id = models.CharField(max_length=128, blank=True)
    artifact_metadata = models.JSONField(default=dict, blank=True)
    workspace_capabilities = models.JSONField(default=list, blank=True)
    mobile_requirement = models.CharField(
        max_length=16,
        choices=Agent.MOBILE_REQUIREMENT_CHOICES,
        default=Agent.MOBILE_DISABLED,
    )
    mobile_capabilities = models.JSONField(default=list, blank=True)
    tool_runtime_policy = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PUBLISHED)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_agent_versions",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["agent", "version"], name="unique_agent_version"),
        ]
        indexes = [
            models.Index(fields=["agent", "status", "created_at"]),
        ]


class AgentDeployment(SoftDeleteModel):
    STATUS_DEPLOYING = "deploying"
    STATUS_ACTIVE = "active"
    STATUS_STOPPED = "stopped"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = (
        (STATUS_DEPLOYING, "Deploying"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_STOPPED, "Stopped"),
        (STATUS_FAILED, "Failed"),
    )

    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="deployments")
    version = models.ForeignKey(AgentVersion, on_delete=models.SET_NULL, null=True, blank=True, related_name="deployments")
    env = models.CharField(max_length=64, default="prod")
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_DEPLOYING)
    endpoint_url = models.URLField(max_length=1024, blank=True)
    deployed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_deployments",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["agent", "env"], name="unique_agent_env_deployment"),
        ]
        indexes = [
            models.Index(fields=["agent", "env", "status"]),
        ]


class AgentLog(models.Model):
    LEVEL_INFO = "info"
    LEVEL_ERROR = "error"
    LEVEL_CHOICES = (
        (LEVEL_INFO, "Info"),
        (LEVEL_ERROR, "Error"),
    )

    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="logs")
    deployment = models.ForeignKey(AgentDeployment, on_delete=models.SET_NULL, null=True, blank=True, related_name="logs")
    level = models.CharField(max_length=16, choices=LEVEL_CHOICES, default=LEVEL_INFO)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["agent", "created_at"]),
            models.Index(fields=["agent", "-created_at", "-id"], name="agent_log_page_idx"),
        ]


class AgentMemoryItem(SoftDeleteModel):
    SCOPE_CALLER = "caller"
    SCOPE_AGENT_GLOBAL = "agent_global"
    SCOPE_DEVELOPER_ONLY = "developer_only"
    SCOPE_CHOICES = (
        (SCOPE_CALLER, "Caller"),
        (SCOPE_AGENT_GLOBAL, "Agent global"),
        (SCOPE_DEVELOPER_ONLY, "Developer only"),
    )
    TYPE_FACT = "fact"
    TYPE_PREFERENCE = "preference"
    TYPE_WORKFLOW = "workflow"
    TYPE_DOMAIN_KNOWLEDGE = "domain_knowledge"
    TYPE_USER_PROFILE = "user_profile"
    TYPE_CHOICES = (
        (TYPE_FACT, "Fact"),
        (TYPE_PREFERENCE, "Preference"),
        (TYPE_WORKFLOW, "Workflow"),
        (TYPE_DOMAIN_KNOWLEDGE, "Domain knowledge"),
        (TYPE_USER_PROFILE, "User profile"),
    )

    SENSITIVITY_PUBLIC = "public"
    SENSITIVITY_INTERNAL = "internal"
    SENSITIVITY_CONFIDENTIAL = "confidential"
    SENSITIVITY_RESTRICTED = "restricted"
    SENSITIVITY_CHOICES = (
        (SENSITIVITY_PUBLIC, "Public"),
        (SENSITIVITY_INTERNAL, "Internal"),
        (SENSITIVITY_CONFIDENTIAL, "Confidential"),
        (SENSITIVITY_RESTRICTED, "Restricted"),
    )

    CONSENT_PENDING = "pending"
    CONSENT_APPROVED = "approved"
    CONSENT_REVOKED = "revoked"
    CONSENT_CHOICES = (
        (CONSENT_PENDING, "Pending"),
        (CONSENT_APPROVED, "Approved"),
        (CONSENT_REVOKED, "Revoked"),
    )

    LICENSE_UNKNOWN = "unknown"
    LICENSE_INTERNAL = "internal"
    LICENSE_APPROVED = "approved"
    LICENSE_CHOICES = (
        (LICENSE_UNKNOWN, "Unknown"),
        (LICENSE_INTERNAL, "Internal"),
        (LICENSE_APPROVED, "Approved"),
    )

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="agent_memory_items")
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name="agent_memory_items")
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="memory_items")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_memory_items",
    )
    memory_type = models.CharField(max_length=64, choices=TYPE_CHOICES, default=TYPE_FACT)
    content_text = models.TextField(blank=True)
    content_json = models.JSONField(default=dict, blank=True)
    scope = models.CharField(max_length=32, choices=SCOPE_CHOICES, default=SCOPE_CALLER)
    caller_subject_hash = models.CharField(max_length=64, blank=True, db_index=True)
    source_run = models.ForeignKey(
        "AgentDisplayRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="memory_items",
    )
    source_event_ids = models.JSONField(default=list, blank=True)
    revision = models.PositiveIntegerField(default=1)
    confidence = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("1.0000"))
    sensitivity_level = models.CharField(max_length=32, choices=SENSITIVITY_CHOICES, default=SENSITIVITY_INTERNAL)
    consent_status = models.CharField(max_length=32, choices=CONSENT_CHOICES, default=CONSENT_PENDING)
    license_status = models.CharField(max_length=32, choices=LICENSE_CHOICES, default=LICENSE_UNKNOWN)

    class Meta:
        indexes = [
            models.Index(fields=["agent", "-created_at", "-id"], name="agent_memory_page_idx"),
            models.Index(fields=["tenant", "agent", "status", "created_at"]),
            models.Index(fields=["tenant", "memory_type", "status", "created_at"]),
            models.Index(fields=["tenant", "consent_status", "license_status"]),
        ]

    def save(self, *args, **kwargs):  # type: ignore[override]
        if self.agent_id and not self.tenant_id:
            self.tenant_id = self.agent.tenant_id
        if self.agent_id and not self.project_id:
            self.project_id = self.agent.project_id
        super().save(*args, **kwargs)


class AgentOutputArtifact(SoftDeleteModel):
    SCAN_PENDING = "pending"
    SCAN_PASSED = "passed"
    SCAN_FAILED = "failed"
    SCAN_CHOICES = (
        (SCAN_PENDING, "Pending"),
        (SCAN_PASSED, "Passed"),
        (SCAN_FAILED, "Failed"),
    )

    POLICY_PENDING = "pending"
    POLICY_APPROVED = "approved"
    POLICY_BLOCKED = "blocked"
    POLICY_CHOICES = (
        (POLICY_PENDING, "Pending"),
        (POLICY_APPROVED, "Approved"),
        (POLICY_BLOCKED, "Blocked"),
    )

    LICENSE_UNKNOWN = "unknown"
    LICENSE_INTERNAL = "internal"
    LICENSE_APPROVED = "approved"
    LICENSE_CHOICES = (
        (LICENSE_UNKNOWN, "Unknown"),
        (LICENSE_INTERNAL, "Internal"),
        (LICENSE_APPROVED, "Approved"),
    )

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="agent_output_artifacts")
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name="agent_output_artifacts")
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="output_artifacts")
    run = models.ForeignKey("AgentDisplayRun", on_delete=models.CASCADE, related_name="output_artifacts")
    turn_index = models.PositiveIntegerField(null=True, blank=True)
    runtime = models.ForeignKey(
        "AgentRuntimeDeployment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="output_artifacts",
    )
    source_event = models.ForeignKey(
        "AgentDisplayEvent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="output_artifacts",
    )
    workspace_path = models.CharField(max_length=1024)
    computer_revision = models.PositiveIntegerField(default=0)
    original_file_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=128, blank=True)
    size_bytes = models.BigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    producer_step = models.CharField(max_length=255, blank=True)
    license_status = models.CharField(max_length=32, choices=LICENSE_CHOICES, default=LICENSE_INTERNAL)
    scan_status = models.CharField(max_length=32, choices=SCAN_CHOICES, default=SCAN_PENDING)
    policy_status = models.CharField(max_length=32, choices=POLICY_CHOICES, default=POLICY_PENDING)
    scan_metadata = models.JSONField(default=dict, blank=True)
    snapshot_storage_backend = models.CharField(max_length=32, blank=True)
    snapshot_object_key = models.CharField(max_length=1024, blank=True)
    snapshot_status = models.CharField(max_length=32, default="pending")
    snapshot_error = models.CharField(max_length=1024, blank=True)
    snapshotted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "computer_revision", "workspace_path"], name="unique_agent_run_output_revision"),
        ]
        indexes = [
            models.Index(fields=["tenant", "agent", "run", "status"]),
            models.Index(fields=["run", "-created_at", "-id"], name="agent_output_page_idx"),
            models.Index(fields=["tenant", "scan_status", "policy_status"]),
        ]

    def save(self, *args, **kwargs):  # type: ignore[override]
        if self.agent_id and not self.tenant_id:
            self.tenant_id = self.agent.tenant_id
        if self.agent_id and not self.project_id:
            self.project_id = self.agent.project_id
        if self.run_id and not self.runtime_id:
            self.runtime_id = self.run.runtime_id
        super().save(*args, **kwargs)






class AgentResourceConfig(SoftDeleteModel):
    agent = models.OneToOneField(Agent, on_delete=models.CASCADE, related_name="resource_config")
    cpu = models.CharField(max_length=32)
    memory = models.CharField(max_length=32)


class AgentEndpoint(SoftDeleteModel):
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="endpoints")
    env = models.CharField(max_length=64, default="prod")
    url = models.URLField(max_length=1024)
    status = models.CharField(max_length=32, default=SoftDeleteModel.STATUS_ACTIVE)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["agent", "env"], name="unique_agent_endpoint_env"),
        ]


class AgentRuntimeImage(SoftDeleteModel):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="agent_runtime_images")
    project = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_runtime_images",
    )
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="runtime_images")
    version = models.ForeignKey(
        AgentVersion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runtime_images",
    )
    image_ref = models.CharField(max_length=512)
    image_digest = models.CharField(max_length=128, blank=True)
    artifact_path = models.CharField(max_length=1024, blank=True)
    registry_secret_ref = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_agent_runtime_images",
    )

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "agent", "status"]),
            models.Index(fields=["tenant", "project", "status"]),
        ]


class AgentPythonBuild(BaseModel):
    """Private, immutable source revision and durable, independently claimed build."""
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="python_builds")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    image = models.OneToOneField(AgentRuntimeImage, on_delete=models.SET_NULL, null=True, blank=True, related_name="python_build")
    filename = models.CharField(max_length=128)
    source = models.TextField()
    requirements = models.TextField(blank=True)
    source_sha256 = models.CharField(max_length=64)
    entrypoint = models.CharField(max_length=64)
    framework = models.CharField(max_length=32)
    base_image = models.CharField(max_length=512)
    encrypted_secrets = models.TextField(blank=True)
    status = models.CharField(max_length=24, default="queued")
    stage = models.CharField(max_length=32, default="queued")
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.CharField(max_length=512, blank=True)
    diagnostics = models.JSONField(default=list)
    dependency_lock = models.JSONField(default=list)
    tool_count = models.PositiveIntegerField(default=0)
    worker_id = models.CharField(max_length=128, blank=True)
    target_host = models.CharField(max_length=128, default="local")
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["status", "created_at"], name="agent_python_build_queue")]
        constraints = [models.UniqueConstraint(fields=["agent"], condition=models.Q(status__in=["queued", "running"]), name="agent_one_pending_python_build")]


class EdgeNode(SoftDeleteModel):
    CONNECTION_PENDING = "pending"
    CONNECTION_ONLINE = "online"
    CONNECTION_DEGRADED = "degraded"
    CONNECTION_OFFLINE = "offline"
    CONNECTION_REVOKED = "revoked"
    CONNECTION_CHOICES = (
        (CONNECTION_PENDING, "Pending"),
        (CONNECTION_ONLINE, "Online"),
        (CONNECTION_DEGRADED, "Degraded"),
        (CONNECTION_OFFLINE, "Offline"),
        (CONNECTION_REVOKED, "Revoked"),
    )

    PRESENCE_PROTOCOL_LEGACY = 0
    PRESENCE_PROTOCOL_V1 = 1

    PRESENCE_REASON_PENDING = "pending_heartbeat"
    PRESENCE_REASON_CONNECTED = "connected"
    PRESENCE_REASON_DEGRADED = "degraded"
    PRESENCE_REASON_EXPIRED = "heartbeat_expired"
    PRESENCE_REASON_STOPPED = "graceful_stop"
    PRESENCE_REASON_UPGRADE_REQUIRED = "firmware_upgrade_required"
    PRESENCE_REASON_REVOKED = "revoked"

    IPV6_ROUTED_PREFIX = "routed_prefix"
    IPV6_UPSTREAM_RELAY = "upstream_relay"
    IPV6_AGENT_OWNED = "agent_owned"
    IPV6_MODE_CHOICES = (
        (IPV6_ROUTED_PREFIX, "Routed prefix"),
        (IPV6_UPSTREAM_RELAY, "Upstream relay"),
        (IPV6_AGENT_OWNED, "Agent owned"),
    )

    CONNECTIVITY_DIRECT_IPV6 = "direct_ipv6"
    CONNECTIVITY_RELAY = "relay"
    CONNECTIVITY_CHOICES = (
        (CONNECTIVITY_DIRECT_IPV6, "Direct IPv6"),
        (CONNECTIVITY_RELAY, "Relay"),
    )

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="edge_nodes")
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name="edge_nodes")
    owner_subject_type = models.CharField(max_length=32, blank=True)
    owner_subject_hash = models.CharField(max_length=64, blank=True, db_index=True)
    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="registered_edge_nodes",
    )
    router_id = models.CharField(max_length=128)
    domain_id = models.CharField(max_length=253)
    display_name = models.CharField(max_length=255)
    device_token_hash = models.CharField(max_length=64, unique=True)
    device_cert_thumbprint = models.CharField(max_length=64, blank=True)
    pending_device_cert_thumbprint = models.CharField(max_length=64, blank=True)
    pending_device_cert_expires_at = models.DateTimeField(null=True, blank=True)
    connection_status = models.CharField(max_length=32, choices=CONNECTION_CHOICES, default=CONNECTION_PENDING)
    presence_protocol_version = models.PositiveSmallIntegerField(default=PRESENCE_PROTOCOL_LEGACY)
    last_presence_at = models.DateTimeField(null=True, blank=True)
    presence_expires_at = models.DateTimeField(null=True, blank=True)
    connection_status_reason = models.CharField(max_length=48, default=PRESENCE_REASON_PENDING)
    registration_diagnostic = models.JSONField(default=dict, blank=True)
    ipv6_mode = models.CharField(max_length=32, choices=IPV6_MODE_CHOICES, default=IPV6_ROUTED_PREFIX)
    connectivity_mode = models.CharField(
        max_length=32,
        choices=CONNECTIVITY_CHOICES,
        default=CONNECTIVITY_DIRECT_IPV6,
    )
    relay_id = models.CharField(max_length=64, blank=True)
    relay_assignment_id = models.CharField(max_length=64, blank=True)
    relay_lease_expires_at = models.DateTimeField(null=True, blank=True)
    software_version = models.CharField(max_length=64, blank=True)
    capabilities = models.JSONField(default=dict, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "router_id"], name="unique_tenant_edge_router"),
        ]
        indexes = [
            models.Index(fields=["tenant", "connection_status"], name="agents_edge_tenant_conn_idx"),
            models.Index(fields=["tenant", "project", "status"], name="agents_edge_tenant_proj_idx"),
            models.Index(fields=["tenant", "owner_subject_hash", "status"], name="agents_edge_owner_status_idx"),
            models.Index(fields=["status", "presence_expires_at"], name="agents_edge_presence_exp_idx"),
        ]

    @property
    def presence_supported(self) -> bool:
        return self.presence_protocol_version >= self.PRESENCE_PROTOCOL_V1

    def effective_connection_status(self, *, now=None) -> str:
        if self.connection_status == self.CONNECTION_REVOKED or self.status == self.STATUS_DELETED:
            return self.CONNECTION_REVOKED
        if not self.presence_supported:
            capabilities = self.capabilities if isinstance(self.capabilities, dict) else {}
            if bool(capabilities.get("router_presence_v1")):
                return self.CONNECTION_PENDING
            return self.CONNECTION_OFFLINE
        if self.last_presence_at is None:
            return self.CONNECTION_PENDING
        current = now or timezone.now()
        if self.presence_expires_at is None or self.presence_expires_at <= current:
            return self.CONNECTION_OFFLINE
        if self.connection_status in {self.CONNECTION_ONLINE, self.CONNECTION_DEGRADED}:
            return self.connection_status
        return self.CONNECTION_OFFLINE

    def effective_connection_status_reason(self, *, now=None) -> str:
        if self.connection_status == self.CONNECTION_REVOKED or self.status == self.STATUS_DELETED:
            return self.PRESENCE_REASON_REVOKED
        if not self.presence_supported:
            capabilities = self.capabilities if isinstance(self.capabilities, dict) else {}
            if bool(capabilities.get("router_presence_v1")):
                return self.PRESENCE_REASON_PENDING
            return self.PRESENCE_REASON_UPGRADE_REQUIRED
        if self.last_presence_at is None:
            return self.PRESENCE_REASON_PENDING
        if (
            self.connection_status == self.CONNECTION_OFFLINE
            and self.connection_status_reason
            in {self.PRESENCE_REASON_STOPPED, self.PRESENCE_REASON_EXPIRED}
        ):
            return self.connection_status_reason
        current = now or timezone.now()
        if self.presence_expires_at is None or self.presence_expires_at <= current:
            return self.PRESENCE_REASON_EXPIRED
        return self.connection_status_reason


class EdgePairingCode(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="edge_pairing_codes")
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name="edge_pairing_codes")
    owner_subject_type = models.CharField(max_length=32, blank=True)
    owner_subject_hash = models.CharField(max_length=64, blank=True, db_index=True)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_edge_pairing_codes",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["tenant", "expires_at"], name="agents_pair_tenant_exp_idx")]


class EdgeAgentRegistration(SoftDeleteModel):
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

    TRANSPORT_DIRECT_IPV6 = "direct_ipv6"
    TRANSPORT_RELAY = "relay"
    TRANSPORT_CHOICES = (
        (TRANSPORT_DIRECT_IPV6, "Direct IPv6"),
        (TRANSPORT_RELAY, "Relay"),
    )

    BINDING_MANUAL = "manual"
    BINDING_MANAGED = "managed"
    BINDING_SUPPRESSED = "suppressed"
    BINDING_MODE_CHOICES = (
        (BINDING_MANUAL, "Manual"),
        (BINDING_MANAGED, "OpenWrt managed"),
        (BINDING_SUPPRESSED, "Managed binding suppressed"),
    )

    node = models.ForeignKey(EdgeNode, on_delete=models.CASCADE, related_name="agent_registrations")
    agent = models.OneToOneField(
        Agent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="edge_registration",
    )
    origin = models.CharField(max_length=512)
    route_id = models.CharField(max_length=128)
    protocols = models.JSONField(default=list, blank=True)
    capabilities = models.JSONField(default=list, blank=True)
    mcp_tools = models.JSONField(default=list, blank=True)
    binding_mode = models.CharField(
        max_length=16,
        choices=BINDING_MODE_CHOICES,
        default=BINDING_MANUAL,
    )
    managed_agent_name = models.CharField(max_length=255, blank=True)
    manifest_digest = models.CharField(max_length=64, blank=True)
    # Empty requirement means a legacy connector omitted the SDK contract.
    computer_requirement = models.CharField(
        max_length=16,
        choices=Agent.COMPUTER_REQUIREMENT_CHOICES,
        blank=True,
        default="",
    )
    workspace_capabilities = models.JSONField(default=list, blank=True)
    # Empty requirement means a legacy connector omitted the SDK contract.
    mobile_requirement = models.CharField(
        max_length=16,
        choices=Agent.MOBILE_REQUIREMENT_CHOICES,
        blank=True,
        default="",
    )
    mobile_capabilities = models.JSONField(default=list, blank=True)
    transport = models.CharField(
        max_length=32,
        choices=TRANSPORT_CHOICES,
        default=TRANSPORT_DIRECT_IPV6,
    )
    ipv6_address = models.GenericIPAddressField(protocol="IPv6", null=True, blank=True)
    port = models.PositiveIntegerField(null=True, blank=True)
    path = models.CharField(max_length=255, blank=True, default="")
    scheme = models.CharField(max_length=16, blank=True, default="")
    tls_server_name = models.CharField(max_length=253, blank=True)
    ca_bundle_id = models.CharField(max_length=128, blank=True)
    relay_id = models.CharField(max_length=64, blank=True)
    relay_router_id = models.CharField(max_length=64, blank=True)
    relay_assignment_id = models.CharField(max_length=64, blank=True)
    generation = models.PositiveBigIntegerField(default=1)
    lease_expires_at = models.DateTimeField()
    last_renewed_at = models.DateTimeField()
    last_probe_at = models.DateTimeField(null=True, blank=True)
    health_status = models.CharField(max_length=32, choices=HEALTH_CHOICES, default=HEALTH_UNKNOWN)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["node", "origin"], name="unique_edge_node_agent_origin"),
        ]
        indexes = [
            models.Index(fields=["node", "status", "lease_expires_at"], name="agents_edge_reg_lease_idx"),
            models.Index(fields=["agent", "status"], name="agents_edge_reg_agent_idx"),
        ]

    @property
    def endpoint_url(self) -> str:
        if self.transport == self.TRANSPORT_RELAY:
            return f"relay://{self.relay_id}/{self.relay_router_id}/{self.origin}"
        return f"{self.scheme}://[{self.ipv6_address}]:{self.port}{self.path}"

    def is_effectively_available(self, *, now=None) -> bool:
        """Return whether this registration can accept a call right now."""

        current = now or timezone.now()
        if self.status != SoftDeleteModel.STATUS_ACTIVE or self.lease_expires_at <= current:
            return False
        node = self.node
        if not node.presence_supported:
            # Presence v0 keeps the historical registration-lease behavior.
            return True
        return node.effective_connection_status(now=current) in {
            EdgeNode.CONNECTION_ONLINE,
            EdgeNode.CONNECTION_DEGRADED,
        }


class AgentRuntimeDeployment(SoftDeleteModel):
    STATUS_DEPLOYING = "deploying"
    STATUS_ACTIVE = "active"
    STATUS_STOPPED = "stopped"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = (
        (STATUS_DEPLOYING, "Deploying"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_STOPPED, "Stopped"),
        (STATUS_FAILED, "Failed"),
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

    WORKSPACE_READ_ONLY = "read_only"
    WORKSPACE_READ_WRITE = "read_write"
    WORKSPACE_ACCESS_CHOICES = (
        (WORKSPACE_READ_ONLY, "Read only"),
        (WORKSPACE_READ_WRITE, "Read/write"),
    )

    RUNTIME_DOCKER = "docker"
    RUNTIME_OPENWRT_IPV6 = "openwrt_ipv6"
    RUNTIME_OPENWRT_RELAY = "openwrt_relay"
    RUNTIME_KIND_CHOICES = (
        (RUNTIME_DOCKER, "Docker"),
        (RUNTIME_OPENWRT_IPV6, "OpenWrt IPv6"),
        (RUNTIME_OPENWRT_RELAY, "OpenWrt Relay"),
    )

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="agent_runtime_deployments")
    project = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_runtime_deployments",
    )
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="runtime_deployments")
    agent_deployment = models.ForeignKey(
        AgentDeployment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runtime_deployments",
    )
    runtime_kind = models.CharField(max_length=32, choices=RUNTIME_KIND_CHOICES, default=RUNTIME_DOCKER)
    image = models.ForeignKey(
        AgentRuntimeImage,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="runtime_deployments",
    )
    edge_registration = models.OneToOneField(
        EdgeAgentRegistration,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="runtime_deployment",
    )
    env = models.CharField(max_length=64, default="prod")
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_DEPLOYING)
    container_id = models.CharField(max_length=128, blank=True)
    # Internal desired state, deployment fencing and deferred container retirement.
    # Never contains credentials and is deliberately absent from public serializers.
    docker_lifecycle = models.JSONField(default=dict, blank=True)
    internal_mcp_url = models.URLField(max_length=1024, blank=True)
    health_status = models.CharField(max_length=32, choices=HEALTH_CHOICES, default=HEALTH_UNKNOWN)
    last_error = models.CharField(max_length=1024, blank=True)
    workspace_connection = models.ForeignKey(
        "workspaces.WorkspaceConnection",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_runtime_deployments",
    )
    workspace_root = models.CharField(max_length=1024, blank=True)
    workspace_access_mode = models.CharField(max_length=32, choices=WORKSPACE_ACCESS_CHOICES, default=WORKSPACE_READ_ONLY)
    workspace_token = models.CharField(max_length=128, blank=True)
    deployed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_runtime_deployments",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "agent", "env"], name="unique_agent_runtime_env"),
            models.CheckConstraint(
                condition=(
                    models.Q(runtime_kind="docker", image__isnull=False, edge_registration__isnull=True)
                    | models.Q(runtime_kind="openwrt_ipv6", image__isnull=True, edge_registration__isnull=False)
                    | models.Q(runtime_kind="openwrt_relay", image__isnull=True, edge_registration__isnull=False)
                ),
                name="agent_runtime_kind_target_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "agent", "status"]),
            models.Index(fields=["tenant", "health_status"]),
        ]

    def effective_status(self, *, now=None) -> str:
        """Overlay expiring OpenWrt connectivity on the persisted runtime state."""

        if self.status not in {self.STATUS_ACTIVE, self.STATUS_DEPLOYING}:
            return self.status
        if self.runtime_kind == self.RUNTIME_DOCKER and (self.docker_lifecycle or {}).get("desired") == "stopped":
            return self.STATUS_STOPPED
        if not self.edge_registration_id:
            return self.status
        if not self.edge_registration.is_effectively_available(now=now):
            return self.STATUS_FAILED
        return self.status

    def effective_health_status(self, *, now=None) -> str:
        if (
            self.status in {self.STATUS_ACTIVE, self.STATUS_DEPLOYING}
            and self.effective_status(now=now) == self.STATUS_FAILED
        ):
            return self.HEALTH_UNHEALTHY
        return self.health_status


class AgentComputerBinding(SoftDeleteModel):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="agent_computer_bindings")
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name="agent_computer_bindings")
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="computer_bindings")
    connection = models.ForeignKey(
        "workspaces.WorkspaceConnection",
        on_delete=models.CASCADE,
        related_name="agent_bindings",
    )
    caller_subject_hash = models.CharField(max_length=64, db_index=True)
    caller_principal_type = models.CharField(max_length=32)
    is_default = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "agent", "caller_subject_hash"],
                condition=models.Q(is_default=True, status=SoftDeleteModel.STATUS_ACTIVE),
                name="unique_default_agent_computer_binding",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "caller_subject_hash", "status"], name="agent_comp_tenant_subj_idx"),
            models.Index(fields=["agent", "caller_subject_hash", "status"], name="agent_comp_agent_subj_idx"),
        ]


class AgentWorkspaceGrant(SoftDeleteModel):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="agent_workspace_grants")
    project = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_workspace_grants",
    )
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="workspace_grants")
    caller_subject_hash = models.CharField(max_length=64, db_index=True)
    caller_principal_type = models.CharField(max_length=32)
    scopes = models.JSONField(default=list, blank=True)
    granted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "agent", "caller_subject_hash"],
                condition=models.Q(project__isnull=True),
                name="unique_agent_workspace_grant_tenant",
            ),
            models.UniqueConstraint(
                fields=["tenant", "project", "agent", "caller_subject_hash"],
                condition=models.Q(project__isnull=False),
                name="unique_agent_workspace_grant_project",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "agent", "caller_subject_hash", "status"],
                name="agent_ws_grant_subject_idx",
            ),
        ]


class AgentMobileBinding(SoftDeleteModel):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="agent_mobile_bindings")
    project = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_mobile_bindings",
    )
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="mobile_bindings")
    device = models.ForeignKey(
        "mobile.MobileDevice",
        on_delete=models.CASCADE,
        related_name="agent_bindings",
    )
    caller_subject_hash = models.CharField(max_length=64, db_index=True)
    caller_principal_type = models.CharField(max_length=32)
    is_default = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "agent", "caller_subject_hash"],
                condition=models.Q(project__isnull=True, is_default=True, status=SoftDeleteModel.STATUS_ACTIVE),
                name="uniq_agent_mobile_tenant_def",
            ),
            models.UniqueConstraint(
                fields=["tenant", "project", "agent", "caller_subject_hash"],
                condition=models.Q(project__isnull=False, is_default=True, status=SoftDeleteModel.STATUS_ACTIVE),
                name="uniq_agent_mobile_proj_def",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "caller_subject_hash", "status"], name="agent_mobile_tenant_subj_idx"),
            models.Index(fields=["agent", "caller_subject_hash", "status"], name="agent_mobile_agent_subj_idx"),
        ]


class AgentMobileGrant(SoftDeleteModel):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="agent_mobile_grants")
    project = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_mobile_grants",
    )
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="mobile_grants")
    caller_subject_hash = models.CharField(max_length=64, db_index=True)
    caller_principal_type = models.CharField(max_length=32)
    scopes = models.JSONField(default=list, blank=True)
    granted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "agent", "caller_subject_hash"],
                condition=models.Q(project__isnull=True),
                name="uniq_agent_mobile_grant_ten",
            ),
            models.UniqueConstraint(
                fields=["tenant", "project", "agent", "caller_subject_hash"],
                condition=models.Q(project__isnull=False),
                name="uniq_agent_mobile_grant_proj",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "agent", "caller_subject_hash", "status"],
                name="agent_mobile_grant_subj_idx",
            ),
        ]


class AgentMobileLease(SoftDeleteModel):
    device = models.ForeignKey("mobile.MobileDevice", on_delete=models.CASCADE, related_name="agent_run_leases")
    run = models.OneToOneField("AgentDisplayRun", on_delete=models.CASCADE, related_name="mobile_lease")
    expires_at = models.DateTimeField()
    acquired_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["device"],
                condition=models.Q(status=SoftDeleteModel.STATUS_ACTIVE),
                name="uniq_active_agent_mobile_lease",
            ),
        ]


class AgentDisplayRun(models.Model):
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    REDACTION_PENDING = "pending"
    REDACTION_PASSED = "passed"
    REDACTION_FAILED = "failed"
    STATUS_CHOICES = (
        (STATUS_RUNNING, "Running"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    )
    REDACTION_CHOICES = (
        (REDACTION_PENDING, "Pending"),
        (REDACTION_PASSED, "Passed"),
        (REDACTION_FAILED, "Failed"),
    )

    KIND_INVOCATION = "invocation"
    KIND_DEMO = "demo"
    KIND_DEPLOYMENT = "deployment"
    KIND_LEGACY = "legacy"
    KIND_CHOICES = (
        (KIND_INVOCATION, "Invocation"),
        (KIND_DEMO, "Demo"),
        (KIND_DEPLOYMENT, "Deployment"),
        (KIND_LEGACY, "Legacy"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="agent_display_runs")
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="display_runs")
    runtime = models.ForeignKey(
        AgentRuntimeDeployment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="display_runs",
    )
    consumer_tenant = models.ForeignKey(
        Tenant,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consumed_agent_display_runs",
    )
    consumer_project = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consumed_agent_display_runs",
    )
    computer_binding = models.ForeignKey(
        AgentComputerBinding,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="display_runs",
    )
    mobile_binding = models.ForeignKey(
        AgentMobileBinding,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="display_runs",
    )
    run_kind = models.CharField(max_length=32, choices=KIND_CHOICES, default=KIND_LEGACY)
    caller_principal_type = models.CharField(max_length=32, blank=True)
    caller_principal_id = models.CharField(max_length=128, blank=True)
    caller_subject_hash = models.CharField(max_length=64, blank=True, db_index=True)
    display_token_hash = models.CharField(max_length=64, blank=True)
    display_token_expires_at = models.DateTimeField(null=True, blank=True)
    workspace_root = models.CharField(max_length=1024, blank=True)
    workspace_cwd = models.CharField(max_length=1024, default=".")
    computer_revision = models.PositiveIntegerField(default=0)
    output_root = models.CharField(max_length=1024, blank=True)
    workspace_capabilities_snapshot = models.JSONField(default=list, blank=True)
    workspace_delegate_token_hash = models.CharField(max_length=64, blank=True)
    workspace_delegate_token_expires_at = models.DateTimeField(null=True, blank=True)
    browser_delegate_token_hash = models.CharField(max_length=64, blank=True)
    browser_delegate_token_expires_at = models.DateTimeField(null=True, blank=True)
    mobile_capabilities_snapshot = models.JSONField(default=list, blank=True)
    mobile_delegate_token_hash = models.CharField(max_length=64, blank=True)
    mobile_delegate_token_expires_at = models.DateTimeField(null=True, blank=True)
    interaction_token_hash = models.CharField(max_length=64, blank=True)
    interaction_token_expires_at = models.DateTimeField(null=True, blank=True)
    context_token_hash = models.CharField(max_length=64, blank=True)
    context_token_expires_at = models.DateTimeField(null=True, blank=True)
    interaction_mode = models.CharField(max_length=32, blank=True)
    # Negotiated by the executing SDK over the authenticated Run Context.
    follow_up_mode = models.CharField(max_length=24, default="queue")
    follow_up_attachment_protocol = models.PositiveSmallIntegerField(default=0)
    demo_session_hash = models.CharField(max_length=64, blank=True, db_index=True)
    DISPLAY_TITLE_PENDING = "pending"
    DISPLAY_TITLE_AGENT = "agent"
    DISPLAY_TITLE_DERIVED = "derived"
    DISPLAY_TITLE_USER = "user"
    DISPLAY_TITLE_SOURCE_CHOICES = (
        (DISPLAY_TITLE_PENDING, "Pending"),
        (DISPLAY_TITLE_AGENT, "Agent"),
        (DISPLAY_TITLE_DERIVED, "Derived"),
        (DISPLAY_TITLE_USER, "User"),
    )
    display_title = models.CharField(max_length=80, blank=True)
    display_title_source = models.CharField(
        max_length=16,
        choices=DISPLAY_TITLE_SOURCE_CHOICES,
        default=DISPLAY_TITLE_PENDING,
    )
    caller_hidden_at = models.DateTimeField(null=True, blank=True)
    caller_read_completed_at = models.DateTimeField(null=True, blank=True)
    project_context_snapshot = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    redaction_status = models.CharField(max_length=32, choices=REDACTION_CHOICES, default=REDACTION_PENDING)
    redaction_metadata = models.JSONField(default=dict, blank=True)
    title = models.CharField(max_length=255, blank=True)
    write_token = models.CharField(max_length=128)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["agent", "status", "created_at"]),
            models.Index(fields=["agent", "redaction_status", "created_at"]),
            models.Index(fields=["agent", "-created_at", "-id"], name="agent_run_page_idx"),
            models.Index(fields=["tenant", "created_at"]),
            models.Index(fields=["consumer_tenant", "caller_subject_hash", "created_at"], name="agent_run_consumer_idx"),
            models.Index(fields=["agent", "run_kind", "created_at"], name="agent_run_kind_idx"),
            models.Index(
                fields=["agent", "caller_subject_hash", "caller_hidden_at", "created_at"],
                name="agent_run_history_idx",
            ),
        ]


class AgentRunComputerAttachment(models.Model):
    """Caller-private device lineage; archived sessions are never retargeted."""
    run = models.ForeignKey(AgentDisplayRun, on_delete=models.CASCADE, related_name="computer_attachments")
    revision = models.PositiveIntegerField()
    binding = models.ForeignKey(AgentComputerBinding, null=True, on_delete=models.SET_NULL)
    computer_name = models.CharField(max_length=128, blank=True)
    workspace_root = models.CharField(max_length=1024, blank=True)
    output_root = models.CharField(max_length=1024, blank=True)
    after_event_seq = models.PositiveBigIntegerField(default=0)
    terminal_session = models.ForeignKey("workspaces.WorkspaceTerminalSession", null=True, blank=True, on_delete=models.SET_NULL)
    browser_session = models.ForeignKey("AgentBrowserSession", null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["run", "revision"], name="unique_run_computer_revision")]


class AgentRunMessage(models.Model):
    ROLE_USER = "user"
    ROLE_ASSISTANT = "assistant"
    ROLE_CHOICES = ((ROLE_USER, "User"), (ROLE_ASSISTANT, "Assistant"))

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(AgentDisplayRun, on_delete=models.CASCADE, related_name="messages")
    sequence = models.PositiveIntegerField()
    turn_index = models.PositiveIntegerField(null=True, blank=True)
    role = models.CharField(max_length=16, choices=ROLE_CHOICES)
    content = models.TextField()
    content_blocks = models.JSONField(default=list, blank=True)
    source_message_id = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "sequence"], name="unique_agent_run_message_seq"),
            models.UniqueConstraint(
                fields=["run", "source_message_id"],
                condition=~models.Q(source_message_id=""),
                name="unique_agent_run_source_message",
            ),
        ]
        indexes = [models.Index(fields=["run", "created_at"], name="agent_run_message_idx")]


class AgentProjectContextGrant(SoftDeleteModel):
    """Caller consent to disclose one Project's instructions to an external Agent."""

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="agent_project_context_grants")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="agent_context_grants")
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="project_context_grants")
    caller_principal_type = models.CharField(max_length=32)
    caller_subject_hash = models.CharField(max_length=64, db_index=True)
    granted_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "project", "agent", "caller_subject_hash"],
                condition=models.Q(status=SoftDeleteModel.STATUS_ACTIVE),
                name="unique_active_agent_project_context_grant",
            )
        ]
        indexes = [
            models.Index(
                fields=["tenant", "project", "caller_subject_hash", "status"],
                name="agent_proj_ctx_grant_idx",
            )
        ]


class AgentRunFollowUp(models.Model):
    """Caller-owned durable input. Never replay an already dispatched turn."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(AgentDisplayRun, on_delete=models.CASCADE, related_name="follow_ups")
    idempotency_key = models.CharField(max_length=128)
    fingerprint = models.CharField(max_length=64)
    mode = models.CharField(max_length=16)
    turn_index = models.PositiveIntegerField()
    dispatched_turn = models.PositiveIntegerField(null=True, blank=True)
    content = models.TextField()
    position = models.PositiveBigIntegerField(default=0)
    attachment_manifest = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=24, default="pending")
    code = models.CharField(max_length=64, blank=True)
    encrypted_authority = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    received_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["run", "idempotency_key"], name="agent_follow_up_idempotency")]
        indexes = [models.Index(fields=["status", "mode", "created_at"], name="agent_follow_up_pending")]


class AgentRunContextGrant(models.Model):
    """Single-use encrypted bridge from Nexus to an OpenWrt IPv6 Agent."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        AgentDisplayRun,
        on_delete=models.CASCADE,
        related_name="context_grants",
    )
    agent = models.ForeignKey(
        Agent,
        on_delete=models.CASCADE,
        related_name="run_context_grants",
    )
    edge_registration = models.ForeignKey(
        EdgeAgentRegistration,
        on_delete=models.CASCADE,
        related_name="run_context_grants",
    )
    token_hash = models.CharField(max_length=64, unique=True)
    encrypted_context = models.TextField()
    expires_at = models.DateTimeField()
    redeemed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["run", "expires_at"], name="agent_ctx_run_exp_idx"),
            models.Index(
                fields=["edge_registration", "expires_at"],
                name="agent_ctx_edge_exp_idx",
            ),
        ]


class AgentBrowserSession(models.Model):
    PLATFORM_WINDOWS = "windows"
    PLATFORM_LINUX = "linux"
    PLATFORM_MACOS = "macos"
    PLATFORM_CHOICES = (
        (PLATFORM_WINDOWS, "Windows"),
        (PLATFORM_LINUX, "Linux"),
        (PLATFORM_MACOS, "macOS"),
    )
    STATUS_STARTING = "starting"
    STATUS_ACTIVE = "active"
    STATUS_CLOSED = "closed"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = (
        (STATUS_STARTING, "Starting"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_CLOSED, "Closed"),
        (STATUS_FAILED, "Failed"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.OneToOneField(
        AgentDisplayRun,
        on_delete=models.CASCADE,
        related_name="browser_session",
        null=True,
        blank=True,
    )
    connection = models.ForeignKey(
        "workspaces.WorkspaceConnection",
        on_delete=models.CASCADE,
        related_name="agent_browser_sessions",
    )
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_STARTING,
    )
    platform = models.CharField(
        max_length=16,
        choices=PLATFORM_CHOICES,
        default=PLATFORM_WINDOWS,
    )
    remote_pid = models.PositiveIntegerField(null=True, blank=True)
    remote_port = models.PositiveIntegerField(null=True, blank=True)
    profile_path = models.CharField(max_length=1024, blank=True)
    runtime_session_id = models.CharField(max_length=64, blank=True)
    observation_revision = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=512, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["connection", "status", "started_at"],
                name="agent_browser_connection_idx",
            ),
        ]


class AgentMCPSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="agent_mcp_sessions")
    project = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_mcp_sessions",
    )
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="mcp_sessions")
    runtime = models.ForeignKey(
        AgentRuntimeDeployment,
        on_delete=models.CASCADE,
        related_name="mcp_sessions",
    )
    caller_subject_hash = models.CharField(max_length=64, db_index=True)
    external_session_hash = models.CharField(max_length=64, unique=True)
    internal_session_id = models.CharField(max_length=512)
    client_capabilities_json = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["agent", "caller_subject_hash", "expires_at"],
                name="agent_mcp_session_subject_idx",
            ),
        ]


class AgentDisplayEvent(models.Model):
    TYPE_RUN_STARTED = "RUN_STARTED"
    TYPE_PLAN_UPDATED = "ACTIVITY_SNAPSHOT"
    TYPE_COMPUTER_LOG = "CUSTOM"
    TYPE_COMPUTER_FRAME = "CUSTOM"
    TYPE_TOOL_STARTED = "TOOL_CALL_START"
    TYPE_TOOL_COMPLETED = "TOOL_CALL_END"
    TYPE_FILE_CREATED = "CUSTOM"
    TYPE_FILE_UPDATED = "CUSTOM"
    TYPE_DELIVERABLE_READY = "CUSTOM"
    TYPE_CHAT_MESSAGE = "TEXT_MESSAGE_CONTENT"
    TYPE_RUNTIME_STATUS = "CUSTOM"
    TYPE_RUN_COMPLETED = "RUN_FINISHED"
    TYPE_RUN_FAILED = "RUN_ERROR"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="agent_display_events")
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="display_events")
    run = models.ForeignKey(AgentDisplayRun, on_delete=models.CASCADE, related_name="events")
    seq = models.PositiveIntegerField()
    client_event_id = models.CharField(max_length=128, null=True, blank=True)
    event_type = models.CharField(max_length=96)
    payload_json = models.JSONField(default=dict, blank=True)
    redacted_payload_json = models.JSONField(null=True, blank=True)
    redaction_metadata = models.JSONField(default=dict, blank=True)
    visibility = models.CharField(max_length=32, default="public")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "seq"], name="unique_agent_display_run_seq"),
            models.UniqueConstraint(fields=["run", "client_event_id"], name="unique_agent_run_client_event"),
        ]
        indexes = [
            models.Index(fields=["agent", "created_at"]),
            models.Index(fields=["run", "seq"]),
            models.Index(fields=["event_type", "created_at"]),
        ]


class AgentRunInteraction(models.Model):
    KIND_TEXT = "text"
    KIND_CONFIRM = "confirm"
    KIND_SELECT = "select"
    KIND_CHOICES = (
        (KIND_TEXT, "Text"),
        (KIND_CONFIRM, "Confirm"),
        (KIND_SELECT, "Select"),
    )
    STATUS_PENDING = "pending"
    STATUS_ANSWERED = "answered"
    STATUS_EXPIRED = "expired"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_ANSWERED, "Answered"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_CANCELLED, "Cancelled"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(AgentDisplayRun, on_delete=models.CASCADE, related_name="interactions")
    key = models.CharField(max_length=128)
    kind = models.CharField(max_length=16, choices=KIND_CHOICES, default=KIND_TEXT)
    prompt = models.TextField(blank=True)
    choices_json = models.JSONField(default=list, blank=True)
    response_json = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    expires_at = models.DateTimeField()
    answered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "key"], name="unique_agent_run_interaction_key"),
        ]
        indexes = [
            models.Index(fields=["run", "status", "created_at"], name="agent_run_interaction_idx"),
        ]


class AgentRunCheckpoint(models.Model):
    run = models.OneToOneField(AgentDisplayRun, on_delete=models.CASCADE, related_name="checkpoint")
    stage = models.CharField(max_length=128)
    data_json = models.JSONField(default=dict, blank=True)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class AgentDisplayAsset(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(AgentDisplayRun, on_delete=models.CASCADE, related_name="display_assets")
    file = models.FileField(upload_to=agent_display_asset_upload_to, max_length=1024)
    content_type = models.CharField(max_length=128)
    size_bytes = models.PositiveIntegerField(default=0)
    sha256 = models.CharField(max_length=64)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["run", "created_at"], name="agent_display_asset_idx")]


class AgentExecutionTask(models.Model):
    STATUS_WORKING = "working"
    STATUS_INPUT_REQUIRED = "input_required"
    STATUS_CANCEL_REQUESTED = "cancel_requested"
    STATUS_WAITING_FOR_RUNTIME = "waiting_for_runtime"
    STATUS_RECOVERING = "recovering"
    STATUS_RECOVERY_REQUIRED = "recovery_required"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = (
        (STATUS_WORKING, "Working"),
        (STATUS_INPUT_REQUIRED, "Input required"),
        (STATUS_CANCEL_REQUESTED, "Cancellation requested"),
        (STATUS_WAITING_FOR_RUNTIME, "Waiting for runtime"),
        (STATUS_RECOVERING, "Recovering"),
        (STATUS_RECOVERY_REQUIRED, "Recovery decision required"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.OneToOneField(AgentDisplayRun, on_delete=models.CASCADE, related_name="execution_task")
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="execution_tasks")
    runtime = models.ForeignKey(AgentRuntimeDeployment, on_delete=models.SET_NULL, null=True, blank=True, related_name="execution_tasks")
    caller_subject_hash = models.CharField(max_length=64, db_index=True)
    mcp_session_hash = models.CharField(max_length=64, blank=True)
    tool_name = models.CharField(max_length=128)
    request_json = models.JSONField(default=dict, blank=True)
    result_json = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=96, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_WORKING)
    continuable = models.BooleanField(default=False)
    recovery_protocol = models.PositiveSmallIntegerField(default=0)
    retry_count = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["agent", "caller_subject_hash", "status"], name="agent_exec_task_subject_idx"),
            models.Index(fields=["status", "expires_at"], name="agent_exec_task_expiry_idx"),
        ]


class AgentTaskExecution(models.Model):
    """Durable dispatch envelope. Only ciphertext contains execution input/tokens."""

    task = models.OneToOneField(AgentExecutionTask, primary_key=True, on_delete=models.CASCADE, related_name="execution")
    encrypted_payload = models.TextField(blank=True)
    state = models.CharField(max_length=32, default="queued", db_index=True)
    lease_id = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    cancel_requested_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class AgentRunOperation(models.Model):
    """One deterministic, platform-managed side effect in a Run turn."""

    STATUS_PREPARED = "prepared"
    STATUS_RUNNING = "running"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_FAILED = "failed"
    STATUS_OUTCOME_UNKNOWN = "outcome_unknown"
    STATUS_CHOICES = (
        (STATUS_PREPARED, "Prepared"),
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCEEDED, "Succeeded"),
        (STATUS_FAILED, "Failed"),
        (STATUS_OUTCOME_UNKNOWN, "Outcome unknown"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(AgentDisplayRun, on_delete=models.CASCADE, related_name="operations")
    task = models.ForeignKey(AgentExecutionTask, on_delete=models.CASCADE, related_name="operations")
    turn_index = models.PositiveIntegerField()
    sequence = models.PositiveIntegerField()
    recovery_generation = models.PositiveSmallIntegerField(default=0)
    operation_type = models.CharField(max_length=96)
    request_digest = models.CharField(max_length=64)
    idempotency_key = models.CharField(max_length=128)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_PREPARED)
    result_json = models.JSONField(default=dict, blank=True)
    result_ciphertext = models.TextField(blank=True)
    error_code = models.CharField(max_length=96, blank=True)
    can_reconcile = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["run", "turn_index", "sequence"],
                name="agent_run_operation_sequence_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["task", "status"], name="agent_run_op_task_status_idx"),
            models.Index(fields=["run", "turn_index", "sequence"], name="agent_run_op_order_idx"),
        ]


class AgentRecoveryAttempt(models.Model):
    """Audit-only recovery generation. It never contains caller payloads."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(AgentExecutionTask, on_delete=models.CASCADE, related_name="recovery_attempts")
    turn_index = models.PositiveIntegerField()
    attempt = models.PositiveSmallIntegerField()
    reason = models.CharField(max_length=96)
    status = models.CharField(max_length=32, default="waiting_for_runtime")
    runtime = models.ForeignKey(
        AgentRuntimeDeployment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recovery_attempts",
    )
    agent_version = models.CharField(max_length=32, blank=True)
    tool_contract_digest = models.CharField(max_length=64, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["task", "turn_index", "attempt"],
                name="agent_recovery_attempt_unique",
            ),
        ]
        indexes = [models.Index(fields=["task", "status"], name="agent_recovery_task_status_idx")]


class AgentRuntimeInvocation(models.Model):
    STATUS_PENDING = "pending"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILED, "Failed"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="agent_runtime_invocations")
    project = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_runtime_invocations",
    )
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="runtime_invocations")
    deployment = models.ForeignKey(
        AgentRuntimeDeployment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invocations",
    )
    display_run = models.ForeignKey(
        AgentDisplayRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runtime_invocations",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_runtime_invocations",
    )
    tool_name = models.CharField(max_length=128, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES)
    error_code = models.CharField(max_length=64, blank=True)
    latency_ms = models.PositiveIntegerField(default=0)
    turn_index = models.PositiveIntegerField(default=1)
    request_id = models.CharField(max_length=64, blank=True, db_index=True)
    execution_profile_id = models.CharField(max_length=64, blank=True)
    execution_model = models.CharField(max_length=128, blank=True)
    reasoning_effort = models.CharField(max_length=16, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["display_run", "turn_index"],
                condition=models.Q(display_run__isnull=False),
                name="unique_agent_invocation_run_turn",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "agent", "created_at"]),
            models.Index(fields=["tenant", "status", "created_at"]),
            models.Index(fields=["agent", "tool_name", "display_run"], name="agent_inv_tool_run_idx"),
        ]


class AgentModelUsage(models.Model):
    """Actual model usage reported by an authenticated Run execution."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(AgentDisplayRun, on_delete=models.CASCADE, related_name="model_usage")
    invocation = models.ForeignKey(
        AgentRuntimeInvocation,
        on_delete=models.CASCADE,
        related_name="model_usage",
    )
    turn_index = models.PositiveIntegerField(default=1)
    event_id = models.CharField(max_length=128)
    profile_id = models.CharField(max_length=64, blank=True)
    model = models.CharField(max_length=128)
    input_tokens = models.PositiveBigIntegerField(default=0)
    output_tokens = models.PositiveBigIntegerField(default=0)
    cached_input_tokens = models.PositiveBigIntegerField(default=0)
    reasoning_tokens = models.PositiveBigIntegerField(default=0)
    context_window = models.PositiveBigIntegerField()
    primary = models.BooleanField(default=True)
    source = models.CharField(max_length=32, default="sdk")
    payload_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "event_id"], name="unique_agent_usage_event"),
        ]
        indexes = [
            models.Index(fields=["run", "turn_index", "created_at"], name="agent_usage_run_turn_idx"),
        ]


def __getattr__(name):
    if name in {"AgentPricing", "AgentToolPricingLimit", "APIKey"}:
        from .model_extension import model_extension
        return getattr(model_extension(), name)
    raise AttributeError(name)
