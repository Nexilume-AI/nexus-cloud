import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("personal", "0006_gateway_operation"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(name="PersonalRouterCredential", fields=[
        ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
        ("token_hash", models.CharField(max_length=64, unique=True)),
        ("model_names", models.JSONField(default=list)),
        ("created_at", models.DateTimeField(auto_now_add=True)),
        ("expires_at", models.DateTimeField()),
        ("revoked_at", models.DateTimeField(null=True, blank=True)),
        ("last_used_at", models.DateTimeField(null=True, blank=True)),
        ("owner", models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.CASCADE)),
        ("tenant", models.ForeignKey(to="tenancy.tenant", on_delete=django.db.models.deletion.CASCADE)),
        ("project", models.ForeignKey(to="tenancy.project", on_delete=django.db.models.deletion.CASCADE)),
        ("router", models.ForeignKey(to="routers.router", related_name="personal_credentials", on_delete=django.db.models.deletion.CASCADE)),
    ])]
