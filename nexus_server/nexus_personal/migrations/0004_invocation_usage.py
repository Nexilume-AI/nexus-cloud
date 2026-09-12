from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("personal", "0003_run_reservation"), ("agents", "0002_personal_runtime")]
    operations = [migrations.CreateModel(name="PersonalInvocationUsage", fields=[
        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
        ("run_id", models.UUIDField()),
        ("turn_index", models.PositiveIntegerField()),
        ("started_at", models.DateTimeField(db_index=True)),
        ("expires_at", models.DateTimeField(db_index=True)),
        ("finished_at", models.DateTimeField(blank=True, null=True)),
        ("latency_ms", models.PositiveBigIntegerField(default=0)),
        ("reserved_ms", models.PositiveBigIntegerField()),
        ("invocation", models.OneToOneField(null=True, on_delete=django.db.models.deletion.SET_NULL,
            to="agents.agentruntimeinvocation")),
        ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="tenancy.tenant")),
    ], options={"constraints": [models.UniqueConstraint(fields=("run_id", "turn_index"),
        name="personal_invocation_usage_turn")]})]
