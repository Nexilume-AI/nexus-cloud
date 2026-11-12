from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("routers", "0002_routercredentialvault")]
    operations = [
        migrations.AddField(model_name="router", name="decision_config", field=models.JSONField(blank=True, default=dict)),
        migrations.AlterField(model_name="router", name="strategy", field=models.CharField(default="cost", max_length=32, choices=[
            ("cost", "Cost"), ("quality", "Quality"), ("custom", "Custom"), ("manual_priority", "Manual priority"),
            ("lowest_cost_pool", "Lowest cost pool"), ("lowest_latency_pool", "Lowest latency pool"),
            ("best_health_pool", "Best health pool"), ("task_type", "Task type"), ("system_one", "System One decision")])),
    ]
