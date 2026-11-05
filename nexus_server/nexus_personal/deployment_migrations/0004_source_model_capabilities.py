from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("deployments", "0003_source_model_reference")]
    operations = [migrations.AddField(model_name="deployment", name="model_capabilities_override",
                                     field=models.JSONField(null=True, blank=True, default=None))]
