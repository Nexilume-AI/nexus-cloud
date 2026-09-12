from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("personal", "0005_gateway_requests")]
    operations = [migrations.AddField(model_name="personalgatewayrequest", name="operation",
        field=models.CharField(default="chat.completions", max_length=32))]
