import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("routers", "0001_personal_models"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [migrations.CreateModel(
        name="RouterCredentialVault",
        fields=[
            ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("backend", models.CharField(max_length=255)),
            ("credential_id", models.UUIDField()),
            ("encrypted_key", models.TextField()),
            ("router", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="credential_vaults", to="routers.router")),
            ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ],
        options={"constraints": [models.UniqueConstraint(
            fields=("router", "owner", "backend"), name="router_credential_vault_owner",
        )]},
    )]
