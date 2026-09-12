from __future__ import annotations

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ExternalIdentity",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("provider", models.CharField(max_length=32)),
                ("subject", models.CharField(max_length=255)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="external_identities", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "indexes": [models.Index(fields=["provider", "email"], name="accounts_ex_provide_3baf03_idx")],
                "constraints": [
                    models.UniqueConstraint(fields=("provider", "subject"), name="accounts_external_provider_subject_uniq"),
                    models.UniqueConstraint(fields=("user", "provider"), name="accounts_external_user_provider_uniq"),
                ],
            },
        ),
    ]
