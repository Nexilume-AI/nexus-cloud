from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('agents', '0009_interactive_views')]
    operations = [
        migrations.AddField(model_name='agentdisplayrun', name='mobile_mcp_preapproved', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='agentcredentialauthorization', name='requested_at', field=models.DateTimeField(null=True, blank=True)),
    ]
