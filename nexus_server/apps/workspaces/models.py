from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import SoftDeleteModel
from apps.tenancy.models import Project, Tenant


class WorkspaceConnection(SoftDeleteModel):
    TYPE_SSH = "ssh"
    TYPE_RUNTIME = "runtime"
    TYPE_CHOICES = (
        (TYPE_RUNTIME, "Nexus Computer Runtime"),
        (TYPE_SSH, "Legacy SSH"),
    )

    AUTH_PRIVATE_KEY = "private_key"
    AUTH_PASSWORD = "password"
    AUTH_CHOICES = (
        (AUTH_PRIVATE_KEY, "Private Key"),
        (AUTH_PASSWORD, "Password"),
    )

    TEST_UNKNOWN = "unknown"
    TEST_SUCCEEDED = "succeeded"
    TEST_FAILED = "failed"
    TEST_CHOICES = (
        (TEST_UNKNOWN, "Unknown"),
        (TEST_SUCCEEDED, "Succeeded"),
        (TEST_FAILED, "Failed"),
    )

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="workspace_connections")
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name="workspace_connections")
    owner_subject_type = models.CharField(max_length=32, blank=True)
    owner_subject_hash = models.CharField(max_length=64, blank=True, db_index=True)
    workspace_root = models.CharField(max_length=1024, default="~/.nexus")
    name = models.CharField(max_length=128)
    connection_type = models.CharField(max_length=32, choices=TYPE_CHOICES, default=TYPE_SSH)
    ssh_host = models.CharField(max_length=255)
    ssh_port = models.PositiveIntegerField(default=22)
    ssh_user = models.CharField(max_length=128)
    auth_mode = models.CharField(max_length=32, choices=AUTH_CHOICES, default=AUTH_PRIVATE_KEY)
    encrypted_private_key = models.TextField(blank=True)
    encrypted_password = models.TextField(blank=True)
    last_test_status = models.CharField(max_length=32, choices=TEST_CHOICES, default=TEST_UNKNOWN)
    last_test_error = models.CharField(max_length=1024, blank=True)
    last_test_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_workspace_connections",
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "owner_subject_hash", "name"],
                condition=models.Q(status=SoftDeleteModel.STATUS_ACTIVE),
                name="unique_active_owned_workspace_name",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "status", "created_at"]),
            models.Index(fields=["tenant", "project", "status"]),
            models.Index(fields=["tenant", "ssh_host", "ssh_user"]),
        ]

    def __str__(self) -> str:
        if self.connection_type == self.TYPE_RUNTIME:
            return f"{self.name} (Nexus Computer Runtime)"
        return f"{self.name} ({self.ssh_user}@{self.ssh_host})"


class ComputerRuntimeEnrollment(models.Model):
    """One-time, caller-owned pairing grant for a Computer Runtime."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="computer_runtime_enrollments")
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name="computer_runtime_enrollments")
    connection = models.OneToOneField(
        WorkspaceConnection,
        on_delete=models.CASCADE,
        related_name="runtime_enrollment",
    )
    owner_subject_type = models.CharField(max_length=32)
    owner_subject_hash = models.CharField(max_length=64, db_index=True)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_computer_runtime_enrollments",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "owner_subject_hash", "expires_at"], name="computer_enroll_owner_exp_idx"),
        ]


class ComputerRuntimeDevice(models.Model):
    """Public device identity and live capability facts for one paired user Runtime."""

    PLATFORM_WINDOWS = "windows"
    PLATFORM_LINUX = "linux"
    PLATFORM_MACOS = "macos"
    PLATFORM_CHOICES = (
        (PLATFORM_WINDOWS, "Windows"),
        (PLATFORM_LINUX, "Linux"),
        (PLATFORM_MACOS, "macOS"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.OneToOneField(
        WorkspaceConnection,
        on_delete=models.CASCADE,
        related_name="runtime_device",
    )
    public_key_pem = models.TextField()
    public_key_fingerprint = models.CharField(max_length=64, unique=True)
    platform = models.CharField(max_length=16, choices=PLATFORM_CHOICES)
    protocol_version = models.PositiveSmallIntegerField(default=1)
    capabilities = models.JSONField(default=dict, blank=True)
    facts = models.JSONField(default=dict, blank=True)
    generation = models.PositiveBigIntegerField(default=0)
    last_server_sequence = models.PositiveBigIntegerField(default=0)
    last_client_sequence = models.PositiveBigIntegerField(default=0)
    auth_challenge_hash = models.CharField(max_length=64, blank=True)
    auth_challenge_expires_at = models.DateTimeField(null=True, blank=True)
    connect_ticket_hash = models.CharField(max_length=64, blank=True)
    connect_ticket_expires_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["last_seen_at", "revoked_at"], name="computer_runtime_presence_idx"),
        ]

    @property
    def online(self) -> bool:
        if self.revoked_at is not None or self.last_seen_at is None:
            return False
        stale_seconds = max(int(getattr(settings, "NEXUS_COMPUTER_RUNTIME_STALE_SECONDS", 45)), 10)
        return (timezone.now() - self.last_seen_at).total_seconds() <= stale_seconds


class ComputerRuntimeCommand(models.Model):
    """Durable, idempotent command routed to one outbound Computer Runtime."""

    STATUS_QUEUED = "queued"
    STATUS_DISPATCHED = "dispatched"
    STATUS_RUNNING = "running"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_FAILED = "failed"
    STATUS_CANCELED = "canceled"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = (
        (STATUS_QUEUED, "Queued"),
        (STATUS_DISPATCHED, "Dispatched"),
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCEEDED, "Succeeded"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELED, "Canceled"),
        (STATUS_EXPIRED, "Expired"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device = models.ForeignKey(ComputerRuntimeDevice, on_delete=models.CASCADE, related_name="commands")
    connection = models.ForeignKey(WorkspaceConnection, on_delete=models.CASCADE, related_name="runtime_commands")
    display_run_id = models.UUIDField(null=True, blank=True, db_index=True)
    caller_subject_hash = models.CharField(max_length=64, blank=True, db_index=True)
    required_scope = models.CharField(max_length=64)
    operation = models.CharField(max_length=96)
    idempotency_key = models.CharField(max_length=128, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    encrypted_payload = models.TextField()
    payload_summary = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    encrypted_result = models.TextField(blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.CharField(max_length=1024, blank=True)
    server_sequence = models.PositiveBigIntegerField(default=0)
    expires_at = models.DateTimeField()
    dispatched_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["device", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="unique_computer_runtime_idempotency",
            ),
        ]
        indexes = [
            models.Index(fields=["device", "status", "created_at"], name="computer_command_queue_idx"),
            models.Index(fields=["device", "expires_at"], name="computer_command_expiry_idx"),
        ]




class WorkspaceTerminalSession(SoftDeleteModel):
    STATUS_CREATED = "created"
    STATUS_ACTIVE = "active"
    STATUS_CLOSED = "closed"
    STATUS_FAILED = "failed"
    SESSION_STATUS_CHOICES = (
        (STATUS_CREATED, "Created"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_CLOSED, "Closed"),
        (STATUS_FAILED, "Failed"),
        (SoftDeleteModel.STATUS_DELETED, "Deleted"),
    )

    SHELL_AUTO = "auto"
    SHELL_POWERSHELL = "powershell"
    SHELL_SH = "sh"
    SHELL_BASH = "bash"
    SHELL_CHOICES = (
        (SHELL_AUTO, "Auto"),
        (SHELL_POWERSHELL, "PowerShell"),
        (SHELL_SH, "sh"),
        (SHELL_BASH, "bash"),
    )

    KIND_USER = "user"
    KIND_AGENT_RUN = "agent_run"
    KIND_CHOICES = (
        (KIND_USER, "User"),
        (KIND_AGENT_RUN, "Agent run"),
    )

    status = models.CharField(max_length=32, choices=SESSION_STATUS_CHOICES, default=STATUS_CREATED)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="workspace_terminal_sessions")
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name="workspace_terminal_sessions")
    connection = models.ForeignKey(WorkspaceConnection, on_delete=models.CASCADE, related_name="terminal_sessions")
    display_run = models.OneToOneField(
        "agents.AgentDisplayRun",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="terminal_session",
    )
    computer_binding = models.ForeignKey(
        "agents.AgentComputerBinding",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="terminal_sessions",
    )
    session_kind = models.CharField(max_length=32, choices=KIND_CHOICES, default=KIND_USER)
    caller_subject_hash = models.CharField(max_length=64, blank=True, db_index=True)
    authorized_root = models.CharField(max_length=1024, blank=True)
    output_root = models.CharField(max_length=1024, blank=True)
    viewer_mode = models.CharField(max_length=32, default="read_only")
    shell = models.CharField(max_length=32, choices=SHELL_CHOICES, default=SHELL_AUTO)
    cols = models.PositiveIntegerField(default=100)
    rows = models.PositiveIntegerField(default=30)
    last_error = models.CharField(max_length=1024, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_workspace_terminal_sessions",
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "status", "created_at"]),
            models.Index(fields=["tenant", "project", "status"]),
            models.Index(fields=["connection", "status"]),
        ]

    def __str__(self) -> str:
        return f"terminal:{self.connection.name}:{self.status}"


class WorkspaceTerminalTranscript(models.Model):
    KIND_COMMAND = "command"
    KIND_STDOUT = "stdout"
    KIND_STDERR = "stderr"
    KIND_SYSTEM = "system"
    KIND_EXIT = "exit"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(WorkspaceTerminalSession, on_delete=models.CASCADE, related_name="transcript")
    seq = models.PositiveIntegerField()
    kind = models.CharField(max_length=16)
    command_id = models.CharField(max_length=64, blank=True)
    data = models.TextField(blank=True)
    exit_code = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["session", "seq"], name="unique_workspace_terminal_seq"),
        ]
        indexes = [models.Index(fields=["session", "seq"], name="workspaces_transcript_seq_idx")]


def __getattr__(name):
    if name == "WorkspaceToolManagedProfile":
        from django.apps import apps
        try:
            return apps.get_model("workspaces", name, require_ready=False)
        except LookupError:
            # Never implicitly install an integration after app composition.
            raise AttributeError(name) from None
    raise AttributeError(name)
