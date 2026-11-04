from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("deployments", "0002_personal_models")]
    operations = [migrations.AddField(model_name="deployment", name="models_dev_mapping",
                                      field=models.JSONField(null=True, blank=True, default=None))]
