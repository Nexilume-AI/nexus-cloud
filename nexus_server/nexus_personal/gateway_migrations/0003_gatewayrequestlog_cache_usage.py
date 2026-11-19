from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("gateway", "0002_gatewaydecisionoperation")]
    operations = [migrations.AddField(model_name="gatewayrequestlog", name="cache_usage", field=models.JSONField(default=dict, blank=True))]
