from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('agents', '0006_agent_background_events')]
    operations = [migrations.AddField(model_name='agentcredentialauthorization', name='preauthorized',
                  field=models.BooleanField(default=False, db_default=False))]
