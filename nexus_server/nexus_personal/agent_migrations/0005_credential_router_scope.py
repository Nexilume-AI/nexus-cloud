from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('agents', '0004_credential_endpoint_scope')]
    operations = [
        migrations.AddField(model_name='agentcredentialauthorization', name='router_id', field=models.UUIDField(null=True, blank=True)),
        migrations.AddField(model_name='agentcredentialauthorization', name='issued_key_id', field=models.UUIDField(null=True, blank=True, unique=True)),
    ]
