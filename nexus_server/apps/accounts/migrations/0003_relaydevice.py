import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("accounts", "0002_externalidentity"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(name="RelayDevice", fields=[
        ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
        ("created_at", models.DateTimeField(auto_now_add=True)),
        ("updated_at", models.DateTimeField(auto_now=True)),
        ("user", models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ("name", models.CharField(max_length=100)),
        ("user_code_hash", models.CharField(max_length=64, unique=True)),
        ("device_code_hash", models.CharField(max_length=64, unique=True)),
        ("status", models.CharField(max_length=16, default="pending", db_index=True)),
        ("approval_expires_at", models.DateTimeField()),
        ("last_poll_at", models.DateTimeField(null=True)),
        ("tenant_id", models.CharField(max_length=64, blank=True)),
        ("password_hash", models.CharField(max_length=64, blank=True)),
        ("manifest", models.JSONField(default=list)),
        ("registrations", models.JSONField(default=dict)),
        ("access_hash", models.CharField(max_length=64, null=True, unique=True)),
        ("refresh_hash", models.CharField(max_length=64, null=True, unique=True)),
        ("access_expires_at", models.DateTimeField(null=True)),
        ("refresh_expires_at", models.DateTimeField(null=True)),
        ("expires_at", models.DateTimeField(null=True)),
        ("revoked_at", models.DateTimeField(null=True)),
    ])]
