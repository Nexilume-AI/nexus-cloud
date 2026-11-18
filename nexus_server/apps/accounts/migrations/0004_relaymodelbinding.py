import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_relaydevice"), ("providers", "0001_personal_models")]
    operations = [migrations.CreateModel(name="RelayModelBinding", fields=[
        ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
        ("created_at", models.DateTimeField(auto_now_add=True)),
        ("updated_at", models.DateTimeField(auto_now=True)),
        ("upstream_url", models.URLField(max_length=1024)),
        ("protocol", models.CharField(max_length=32)),
        ("models", models.JSONField(default=list)),
        ("status", models.CharField(default="prepared", max_length=16)),
        ("original", models.JSONField(default=dict)),
        ("device", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="model_bindings", to="accounts.relaydevice")),
        ("provider", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="relay_binding", to="providers.provideraccount")),
    ])]
