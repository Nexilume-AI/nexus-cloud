from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [("agents", "0002_personal_runtime"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(
        name="AgentCredentialAuthorization",
        fields=[
            ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
            ("provider", models.CharField(max_length=32)),
            ("status", models.CharField(max_length=16, default="pending")),
            ("encrypted_secret", models.TextField(blank=True)),
            ("expires_at", models.DateTimeField()),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ("run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="credential_authorizations", to="agents.agentdisplayrun")),
        ], options={"constraints": [models.UniqueConstraint(fields=("run", "provider"), name="agent_credential_run_provider")]})]
