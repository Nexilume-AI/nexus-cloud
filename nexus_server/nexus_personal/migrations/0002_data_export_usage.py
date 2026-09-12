from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("personal", "0001_initial"),
        ("datasets", "0001_personal_datasets"),
    ]
    operations = [
        migrations.CreateModel(
            name="PersonalDataExportUsage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("idempotency_key", models.CharField(max_length=128, unique=True)),
                ("size_bytes", models.PositiveBigIntegerField()),
                ("recorded_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("transfer", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                                                  to="datasets.datasettransfer")),
            ],
        ),
    ]
