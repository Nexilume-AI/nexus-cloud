import uuid
from django.db import migrations, models


def search_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for name, expression in [
        ("inbox_kind_search", "UPPER(kind::text)"),
        ("inbox_title_search", "UPPER(title_key::text)"),
        ("inbox_resource_search", "UPPER((safe_context ->> 'resource_name')::text)"),
        ("inbox_agent_search", "UPPER((safe_context ->> 'agent_name')::text)"),
    ]:
        schema_editor.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON notifications_inboxitem USING gin (({expression}) gin_trgm_ops)")
    schema_editor.execute("""CREATE INDEX CONCURRENTLY IF NOT EXISTS inbox_page_order ON notifications_inboxitem
        (tenant_id, priority DESC, (COALESCE(due_at, '9999-12-31 23:59:59.999999+00'::timestamptz)), occurred_at DESC, id DESC)""")


def remove_search_indexes(apps, schema_editor):
    for name in ("inbox_kind_search", "inbox_title_search", "inbox_resource_search", "inbox_agent_search", "inbox_page_order"):
        schema_editor.execute(f"DROP INDEX IF EXISTS {name}")


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("notifications", "0002_work_inbox")]

    operations = [
        migrations.CreateModel(name="InboxWorkerCursor", fields=[
            ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("name", models.CharField(max_length=80, unique=True)),
            ("position", models.JSONField(default=dict)),
        ]),
        migrations.AddField(model_name="pushdelivery", name="lease_token", field=models.UUIDField(null=True, blank=True)),
        migrations.AddField(model_name="pushdelivery", name="lease_expires_at", field=models.DateTimeField(null=True, blank=True)),
        migrations.AddIndex(model_name="pushdelivery", index=models.Index(fields=["status", "lease_expires_at"], name="push_delivery_lease")),
        migrations.AddIndex(model_name="inboxitem", index=models.Index(fields=["state", "resolved_at"], name="inbox_retention")),
        migrations.AddIndex(model_name="inboxitem", index=models.Index(fields=["tenant", "updated_at"], name="inbox_revision")),
        migrations.RunPython(search_indexes, remove_search_indexes),
    ]
