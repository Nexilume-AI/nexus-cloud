from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.common.models import SoftDeleteModel
from apps.tenancy.models import Project, Tenant


class MobileDevice(SoftDeleteModel):
    PLATFORM_ANDROID = "android"
    PLATFORM_CHOICES = ((PLATFORM_ANDROID, "Android"),)

    APPROVAL_MANUAL = "manual"
    APPROVAL_CONFIRM_HIGH_RISK = "confirm_high_risk"
    APPROVAL_AUTO = "auto"
    APPROVAL_MODE_CHOICES = (
        (APPROVAL_MANUAL, "Manual"),
        (APPROVAL_CONFIRM_HIGH_RISK, "Confirm high risk"),
        (APPROVAL_AUTO, "Auto"),
    )

    ONLINE_UNKNOWN = "unknown"
    ONLINE_ONLINE = "online"
    ONLINE_OFFLINE = "offline"
    ONLINE_CHOICES = (
        (ONLINE_UNKNOWN, "Unknown"),
        (ONLINE_ONLINE, "Online"),
        (ONLINE_OFFLINE, "Offline"),
    )

    LIFECYCLE_AWAITING_PAIRING = "awaiting_pairing"
    LIFECYCLE_SETUP_REQUIRED = "setup_required"
    LIFECYCLE_ONLINE = "online"
    LIFECYCLE_OFFLINE = "offline"
    LIFECYCLE_TOKEN_EXPIRED = "token_expired"
    LIFECYCLE_DISABLED = "disabled"

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="mobile_devices")
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name="mobile_devices")
    name = models.CharField(max_length=128)
    platform = models.CharField(max_length=32, choices=PLATFORM_CHOICES, default=PLATFORM_ANDROID)
    device_identifier = models.CharField(max_length=255, blank=True)
    token_prefix = models.CharField(max_length=32, db_index=True)
    token_hash = models.CharField(max_length=64, unique=True)
    approval_mode = models.CharField(max_length=32, choices=APPROVAL_MODE_CHOICES, default=APPROVAL_CONFIRM_HIGH_RISK)
    online_status = models.CharField(max_length=32, choices=ONLINE_CHOICES, default=ONLINE_UNKNOWN)
    capabilities = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    current_package = models.CharField(max_length=255, blank=True)
    current_activity = models.CharField(max_length=255, blank=True)
    last_observation = models.JSONField(default=dict, blank=True)
    last_screenshot = models.BinaryField(null=True, blank=True, editable=False)
    last_screenshot_content_type = models.CharField(max_length=32, blank=True)
    last_screenshot_captured_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    owner_subject_hash = models.CharField(max_length=64, blank=True, db_index=True)
    owner_principal_type = models.CharField(max_length=32, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_mobile_devices",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "owner_subject_hash", "name", "status"],
                name="unique_caller_mobile_device_name_status",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "status", "created_at"]),
            models.Index(fields=["tenant", "project", "status"]),
            models.Index(fields=["tenant", "online_status", "last_seen_at"]),
        ]

    def mark_seen(self) -> None:
        self.online_status = self.ONLINE_ONLINE
        self.last_seen_at = timezone.now()

    def metadata_datetime(self, key: str):
        value = (self.metadata or {}).get(key)
        if not value:
            return None
        parsed = parse_datetime(str(value))
        if parsed and timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed

    @property
    def paired_at(self):
        return self.metadata_datetime("paired_at")

    @property
    def pairing_expires_at(self):
        return self.metadata_datetime("pairing_token_expires_at")

    @property
    def screenshot_expires_at(self):
        if self.last_screenshot_captured_at is None:
            return None
        ttl_seconds = max(int(getattr(settings, "NEXUS_MOBILE_SCREENSHOT_TTL_SECONDS", 300)), 30)
        return self.last_screenshot_captured_at + timedelta(seconds=ttl_seconds)

    @property
    def screenshot_available(self) -> bool:
        return bool(
            self.last_screenshot
            and self.screenshot_expires_at
            and self.screenshot_expires_at > timezone.now()
        )

    @property
    def lifecycle_status(self) -> str:
        if self.status == self.STATUS_DISABLED:
            return self.LIFECYCLE_DISABLED
        if not self.paired_at and self.pairing_expires_at and self.pairing_expires_at <= timezone.now():
            return self.LIFECYCLE_TOKEN_EXPIRED
        if self.last_seen_at is None:
            return self.LIFECYCLE_AWAITING_PAIRING
        if not bool((self.capabilities or {}).get("accessibility")):
            return self.LIFECYCLE_SETUP_REQUIRED
        stale_seconds = max(int(getattr(settings, "NEXUS_MOBILE_ONLINE_STALE_SECONDS", 90)), 1)
        if self.online_status == self.ONLINE_ONLINE and (timezone.now() - self.last_seen_at).total_seconds() <= stale_seconds:
            return self.LIFECYCLE_ONLINE
        return self.LIFECYCLE_OFFLINE

    @property
    def lifecycle_detail(self) -> str:
        return {
            self.LIFECYCLE_AWAITING_PAIRING: "Scan the pairing QR on the Android device.",
            self.LIFECYCLE_SETUP_REQUIRED: "Enable Android Accessibility control to finish setup.",
            self.LIFECYCLE_ONLINE: "The device is connected and ready for actions.",
            self.LIFECYCLE_OFFLINE: "The device has stopped sending heartbeats.",
            self.LIFECYCLE_TOKEN_EXPIRED: "Generate a new pairing QR to continue.",
            self.LIFECYCLE_DISABLED: "Enable this device before using it.",
        }[self.lifecycle_status]

    @property
    def recommended_action(self) -> str:
        return {
            self.LIFECYCLE_AWAITING_PAIRING: "continue_pairing",
            self.LIFECYCLE_SETUP_REQUIRED: "complete_setup",
            self.LIFECYCLE_ONLINE: "open_control",
            self.LIFECYCLE_OFFLINE: "troubleshoot",
            self.LIFECYCLE_TOKEN_EXPIRED: "regenerate_pairing",
            self.LIFECYCLE_DISABLED: "enable_device",
        }[self.lifecycle_status]

    def __str__(self) -> str:
        return f"{self.name} ({self.platform})"


class MobileCommand(SoftDeleteModel):
    STATUS_PENDING_APPROVAL = "pending_approval"
    STATUS_QUEUED = "queued"
    STATUS_RUNNING = "running"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_FAILED = "failed"
    STATUS_REJECTED = "rejected"
    STATUS_CANCELED = "canceled"
    COMMAND_STATUS_CHOICES = (
        (STATUS_PENDING_APPROVAL, "Pending approval"),
        (STATUS_QUEUED, "Queued"),
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCEEDED, "Succeeded"),
        (STATUS_FAILED, "Failed"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_CANCELED, "Canceled"),
        (SoftDeleteModel.STATUS_DELETED, "Deleted"),
    )

    ACTION_OBSERVE = "observe"
    ACTION_TAP_TEXT = "tap_text"
    ACTION_TAP_COORDINATES = "tap_coordinates"
    ACTION_TYPE_TEXT = "type_text"
    ACTION_SWIPE = "swipe"
    ACTION_PRESS_BACK = "press_back"
    ACTION_OPEN_APP = "open_app"
    ACTION_WAIT_FOR_STATE = "wait_for_state"
    ACTION_CAPTURE_SCREEN = "capture_screen"
    ACTION_CHOICES = (
        (ACTION_OBSERVE, "Observe"),
        (ACTION_TAP_TEXT, "Tap text"),
        (ACTION_TAP_COORDINATES, "Tap coordinates"),
        (ACTION_TYPE_TEXT, "Type text"),
        (ACTION_SWIPE, "Swipe"),
        (ACTION_PRESS_BACK, "Press back"),
        (ACTION_OPEN_APP, "Open app"),
        (ACTION_WAIT_FOR_STATE, "Wait for state"),
        (ACTION_CAPTURE_SCREEN, "Capture screen"),
    )

    RISK_LOW = "low"
    RISK_MEDIUM = "medium"
    RISK_HIGH = "high"
    RISK_CHOICES = (
        (RISK_LOW, "Low"),
        (RISK_MEDIUM, "Medium"),
        (RISK_HIGH, "High"),
    )

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="mobile_commands")
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name="mobile_commands")
    device = models.ForeignKey(MobileDevice, on_delete=models.CASCADE, related_name="commands")
    display_run = models.ForeignKey(
        "agents.AgentDisplayRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mobile_commands",
    )
    mobile_binding = models.ForeignKey(
        "agents.AgentMobileBinding",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="commands",
    )
    caller_subject_hash = models.CharField(max_length=64, blank=True, db_index=True)
    client_request_id = models.UUIDField(null=True, blank=True, editable=False)
    action = models.CharField(max_length=64, choices=ACTION_CHOICES)
    arguments = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=32, choices=COMMAND_STATUS_CHOICES, default=STATUS_QUEUED)
    risk_level = models.CharField(max_length=32, choices=RISK_CHOICES, default=RISK_MEDIUM)
    requires_approval = models.BooleanField(default=False)
    result = models.JSONField(default=dict, blank=True)
    screenshot = models.BinaryField(null=True, blank=True, editable=False)
    screenshot_content_type = models.CharField(max_length=32, blank=True)
    error = models.CharField(max_length=1024, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_mobile_commands",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_mobile_commands",
    )

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "status", "created_at"]),
            models.Index(fields=["device", "status", "created_at"]),
            models.Index(fields=["device", "expires_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["display_run", "client_request_id"],
                condition=models.Q(client_request_id__isnull=False),
                name="uniq_mobile_command_run_client_request",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.device.name}:{self.action}:{self.status}"
