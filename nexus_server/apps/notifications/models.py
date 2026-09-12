from django.conf import settings
from django.db import models
from django.utils import timezone
from apps.common.models import BaseModel


class UserNotification(BaseModel):
    """Recipient/read receipt only. Source objects remain the authority for actions."""
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE)
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    agent = models.ForeignKey("agents.Agent", on_delete=models.CASCADE)
    run = models.ForeignKey("agents.AgentDisplayRun", null=True, blank=True, on_delete=models.CASCADE)
    interaction = models.ForeignKey("agents.AgentRunInteraction", null=True, blank=True, on_delete=models.CASCADE)
    build = models.ForeignKey("agents.AgentPythonBuild", null=True, blank=True, on_delete=models.CASCADE)
    job = models.ForeignKey("jobs.Job", null=True, blank=True, on_delete=models.CASCADE)
    kind = models.CharField(max_length=32)
    event_key = models.CharField(max_length=180)
    source_at = models.DateTimeField()
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["recipient", "tenant", "event_key"], name="notice_recipient_event_uniq")]
        indexes = [
            models.Index(fields=["tenant", "recipient", "-created_at", "-id"], name="notice_recipient_timeline"),
            models.Index(fields=["tenant", "recipient", "read_at"], name="notice_recipient_unread"),
        ]


class InboxItem(BaseModel):
    """A safe pointer to work. The source object remains authoritative."""

    CATEGORY_AGENT = "agent"
    CATEGORY_BACKGROUND = "background"
    CATEGORY_APPROVAL = "approval"
    CATEGORY_OPERATIONS = "operations"
    CATEGORY_CHOICES = (
        (CATEGORY_AGENT, "Agent"),
        (CATEGORY_BACKGROUND, "Background tasks"),
        (CATEGORY_APPROVAL, "Approvals"),
        (CATEGORY_OPERATIONS, "Operations"),
    )
    AUDIENCE_PERSONAL = "personal"
    AUDIENCE_ROLE = "role"
    AUDIENCE_CHOICES = ((AUDIENCE_PERSONAL, "Personal"), (AUDIENCE_ROLE, "Shared role queue"))
    STATE_NEEDS_ACTION = "needs_action"
    STATE_IN_PROGRESS = "in_progress"
    STATE_FAILED = "failed"
    STATE_COMPLETED = "completed"
    STATE_RESOLVED = "resolved"
    STATE_CANCELED = "canceled"
    STATE_CHOICES = tuple((value, value.replace("_", " ").title()) for value in (
        STATE_NEEDS_ACTION, STATE_IN_PROGRESS, STATE_FAILED, STATE_COMPLETED, STATE_RESOLVED, STATE_CANCELED,
    ))

    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE, related_name="inbox_items")
    project = models.ForeignKey("tenancy.Project", null=True, blank=True, on_delete=models.CASCADE, related_name="inbox_items")
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="inbox_items")
    category = models.CharField(max_length=24, choices=CATEGORY_CHOICES)
    kind = models.CharField(max_length=64)
    state = models.CharField(max_length=24, choices=STATE_CHOICES)
    priority = models.PositiveSmallIntegerField(default=50)
    audience_type = models.CharField(max_length=16, choices=AUDIENCE_CHOICES, default=AUDIENCE_PERSONAL)
    audience_key = models.CharField(max_length=160)
    required_permission = models.CharField(max_length=128, blank=True)
    source_type = models.CharField(max_length=64)
    source_id = models.CharField(max_length=128)
    event_key = models.CharField(max_length=200)
    navigation_key = models.CharField(max_length=64)
    title_key = models.CharField(max_length=64)
    safe_context = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField()
    due_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "event_key", "audience_key"], name="inbox_event_audience_uniq"),
        ]
        indexes = [
            models.Index(fields=["tenant", "state", "-priority", "-occurred_at"], name="inbox_tenant_state"),
            models.Index(fields=["tenant", "recipient", "-occurred_at"], name="inbox_personal_timeline"),
            models.Index(fields=["tenant", "required_permission", "state"], name="inbox_role_permission"),
            models.Index(fields=["tenant", "source_type", "source_id"], name="inbox_source_lookup"),
            models.Index(fields=["state", "resolved_at"], name="inbox_retention"),
            models.Index(fields=["tenant", "updated_at"], name="inbox_revision"),
        ]


class InboxReceipt(BaseModel):
    item = models.ForeignKey(InboxItem, on_delete=models.CASCADE, related_name="receipts")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="inbox_receipts")
    read_at = models.DateTimeField(null=True, blank=True)
    snoozed_until = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    last_push_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["item", "user"], name="inbox_receipt_user_uniq")]
        indexes = [models.Index(fields=["user", "read_at", "snoozed_until"], name="inbox_receipt_user_state")]


class InboxPreference(BaseModel):
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE, related_name="inbox_preferences")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="inbox_preferences")
    category_preferences = models.JSONField(default=dict, blank=True)
    event_preferences = models.JSONField(default=dict, blank=True)
    timezone = models.CharField(max_length=64, default="UTC")
    dnd_enabled = models.BooleanField(default=False)
    dnd_start = models.TimeField(null=True, blank=True)
    dnd_end = models.TimeField(null=True, blank=True)
    urgent_bypass = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "user"], name="inbox_preference_tenant_user_uniq")]


class WebPushSubscription(BaseModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="web_push_subscriptions")
    endpoint_hash = models.CharField(max_length=64, unique=True)
    endpoint_encrypted = models.TextField()
    p256dh_encrypted = models.TextField()
    auth_encrypted = models.TextField()
    device_name = models.CharField(max_length=80, default="This browser")
    user_agent = models.CharField(max_length=256, blank=True)
    enabled = models.BooleanField(default=True)
    failure_count = models.PositiveSmallIntegerField(default=0)
    disabled_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField()

    def __repr__(self) -> str:
        return f"WebPushSubscription(id={self.pk!s}, enabled={self.enabled!r})"

    class Meta:
        indexes = [models.Index(fields=["user", "enabled"], name="push_sub_user_enabled")]


class PushDelivery(BaseModel):
    STATUS_PENDING = "pending"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_SKIPPED = "skipped"
    STATUS_SENDING = "sending"
    STATUS_EXHAUSTED = "exhausted"
    item = models.ForeignKey(InboxItem, on_delete=models.CASCADE, related_name="push_deliveries")
    subscription = models.ForeignKey(WebPushSubscription, on_delete=models.CASCADE, related_name="deliveries")
    status = models.CharField(max_length=16, default=STATUS_PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=48, blank=True)
    lease_token = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["item", "subscription"], name="push_delivery_item_device_uniq")]
        indexes = [models.Index(fields=["status", "next_attempt_at"], name="push_delivery_due"),
                   models.Index(fields=["status", "lease_expires_at"], name="push_delivery_lease")]


class InboxWorkerCursor(BaseModel):
    """Durable bounded scans. No business content or credentials."""
    name = models.CharField(max_length=80, unique=True)
    position = models.JSONField(default=dict)
