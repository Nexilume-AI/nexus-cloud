from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("personal", "0002_data_export_usage")]
    operations = [migrations.CreateModel(
        name="PersonalRunReservation",
        fields=[
            ("run_id", models.UUIDField(primary_key=True, serialize=False)),
            ("expires_at", models.DateTimeField(db_index=True)),
            ("released_at", models.DateTimeField(blank=True, null=True)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="tenancy.tenant")),
        ],
    )]
