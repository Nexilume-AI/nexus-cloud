import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def migrate_personal_notifications(apps, schema_editor):
    Notice = apps.get_model("notifications", "UserNotification")
    Item = apps.get_model("notifications", "InboxItem")
    Receipt = apps.get_model("notifications", "InboxReceipt")
    state_by_kind = {
        "input_required": "needs_action",
        "run_failed": "failed",
        "run_completed": "completed",
        "build_failed": "failed",
        "build_succeeded": "completed",
        "deployment_failed": "failed",
        "deployment_succeeded": "completed",
    }
    for notice in Notice.objects.select_related("agent", "run").iterator():
        project_id = notice.run.consumer_project_id if notice.run_id else notice.agent.project_id
        source_type = "run" if notice.run_id else ("build" if notice.build_id else "job")
        source_id = notice.run_id or notice.build_id or notice.job_id or notice.id
        state = state_by_kind.get(notice.kind, "resolved")
        item, _ = Item.objects.get_or_create(
            tenant_id=notice.tenant_id,
            event_key=f"legacy:{notice.event_key}",
            audience_key=f"user:{notice.recipient_id}",
            defaults={
                "project_id": project_id,
                "recipient_id": notice.recipient_id,
                "category": "agent" if source_type in {"run", "build"} else "background",
                "kind": notice.kind,
                "state": state,
                "priority": 90 if notice.kind == "input_required" else 70 if notice.kind.endswith("failed") else 40,
                "audience_type": "personal",
                "source_type": source_type,
                "source_id": str(source_id),
                "navigation_key": "agent_run" if notice.run_id else "agent_runtime",
                "title_key": notice.kind,
                "safe_context": {"agent_id": str(notice.agent_id), "agent_name": notice.agent.name},
                "occurred_at": notice.source_at,
                "resolved_at": notice.source_at if state in {"completed", "resolved", "canceled"} else None,
            },
        )
        Receipt.objects.get_or_create(item=item, user_id=notice.recipient_id, defaults={"read_at": notice.read_at})


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0001_initial"),
        ("tenancy", "0001_personal_context"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="InboxItem",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("category", models.CharField(choices=[("agent", "Agent"), ("background", "Background tasks"), ("approval", "Approvals"), ("operations", "Operations")], max_length=24)),
                ("kind", models.CharField(max_length=64)),
                ("state", models.CharField(choices=[("needs_action", "Needs Action"), ("in_progress", "In Progress"), ("failed", "Failed"), ("completed", "Completed"), ("resolved", "Resolved"), ("canceled", "Canceled")], max_length=24)),
                ("priority", models.PositiveSmallIntegerField(default=50)),
                ("audience_type", models.CharField(choices=[("personal", "Personal"), ("role", "Shared role queue")], default="personal", max_length=16)),
                ("audience_key", models.CharField(max_length=160)),
                ("required_permission", models.CharField(blank=True, max_length=128)),
                ("source_type", models.CharField(max_length=64)),
                ("source_id", models.CharField(max_length=128)),
                ("event_key", models.CharField(max_length=200)),
                ("navigation_key", models.CharField(max_length=64)),
                ("title_key", models.CharField(max_length=64)),
                ("safe_context", models.JSONField(blank=True, default=dict)),
                ("occurred_at", models.DateTimeField()),
                ("due_at", models.DateTimeField(blank=True, null=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("project", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="inbox_items", to="tenancy.project")),
                ("recipient", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="inbox_items", to=settings.AUTH_USER_MODEL)),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="inbox_items", to="tenancy.tenant")),
            ],
            options={
                "indexes": [models.Index(fields=["tenant", "state", "-priority", "-occurred_at"], name="inbox_tenant_state"), models.Index(fields=["tenant", "recipient", "-occurred_at"], name="inbox_personal_timeline"), models.Index(fields=["tenant", "required_permission", "state"], name="inbox_role_permission"), models.Index(fields=["tenant", "source_type", "source_id"], name="inbox_source_lookup")],
                "constraints": [models.UniqueConstraint(fields=("tenant", "event_key", "audience_key"), name="inbox_event_audience_uniq")],
            },
        ),
        migrations.CreateModel(
            name="InboxPreference",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("category_preferences", models.JSONField(blank=True, default=dict)),
                ("event_preferences", models.JSONField(blank=True, default=dict)),
                ("timezone", models.CharField(default="UTC", max_length=64)),
                ("dnd_enabled", models.BooleanField(default=False)),
                ("dnd_start", models.TimeField(blank=True, null=True)),
                ("dnd_end", models.TimeField(blank=True, null=True)),
                ("urgent_bypass", models.BooleanField(default=False)),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="inbox_preferences", to="tenancy.tenant")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="inbox_preferences", to=settings.AUTH_USER_MODEL)),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("tenant", "user"), name="inbox_preference_tenant_user_uniq")]},
        ),
        migrations.CreateModel(
            name="WebPushSubscription",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("endpoint_hash", models.CharField(max_length=64, unique=True)),
                ("endpoint_encrypted", models.TextField()),
                ("p256dh_encrypted", models.TextField()),
                ("auth_encrypted", models.TextField()),
                ("device_name", models.CharField(default="This browser", max_length=80)),
                ("user_agent", models.CharField(blank=True, max_length=256)),
                ("enabled", models.BooleanField(default=True)),
                ("failure_count", models.PositiveSmallIntegerField(default=0)),
                ("disabled_at", models.DateTimeField(blank=True, null=True)),
                ("last_seen_at", models.DateTimeField()),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="web_push_subscriptions", to=settings.AUTH_USER_MODEL)),
            ],
            options={"indexes": [models.Index(fields=["user", "enabled"], name="push_sub_user_enabled")]},
        ),
        migrations.CreateModel(
            name="InboxReceipt",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("read_at", models.DateTimeField(blank=True, null=True)),
                ("snoozed_until", models.DateTimeField(blank=True, null=True)),
                ("archived_at", models.DateTimeField(blank=True, null=True)),
                ("last_push_at", models.DateTimeField(blank=True, null=True)),
                ("item", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="receipts", to="notifications.inboxitem")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="inbox_receipts", to=settings.AUTH_USER_MODEL)),
            ],
            options={"indexes": [models.Index(fields=["user", "read_at", "snoozed_until"], name="inbox_receipt_user_state")], "constraints": [models.UniqueConstraint(fields=("item", "user"), name="inbox_receipt_user_uniq")]},
        ),
        migrations.CreateModel(
            name="PushDelivery",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("status", models.CharField(default="pending", max_length=16)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("next_attempt_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("error_code", models.CharField(blank=True, max_length=48)),
                ("item", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="push_deliveries", to="notifications.inboxitem")),
                ("subscription", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="deliveries", to="notifications.webpushsubscription")),
            ],
            options={"indexes": [models.Index(fields=["status", "next_attempt_at"], name="push_delivery_due")], "constraints": [models.UniqueConstraint(fields=("item", "subscription"), name="push_delivery_item_device_uniq")]},
        ),
        migrations.RunPython(migrate_personal_notifications, migrations.RunPython.noop),
    ]
