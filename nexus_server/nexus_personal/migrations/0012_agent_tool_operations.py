from django.db import migrations, models


def require_no_agent_writes(apps, schema_editor):
    if apps.get_model('personal','PersonalToolConfigOperation').objects.using(
            schema_editor.connection.alias).filter(section='agents',active=True).exists():
        raise RuntimeError('Recover pending Agent Tool Setup operations before downgrading.')


class Migration(migrations.Migration):
    dependencies=[('personal','0011_agent_mcp_credentials')]
    operations=[
        migrations.AddField(model_name='personaltoolconfigoperation',name='agent_credential_id',field=models.UUIDField(blank=True,null=True)),
        migrations.AddField(model_name='personaltoolconfigoperation',name='old_agent_credential_id',field=models.UUIDField(blank=True,null=True)),
        migrations.AddField(model_name='personaltoolconfigoperation',name='previous_managed_mcp',field=models.JSONField(default=dict)),
        migrations.AddField(model_name='personaltoolconfigoperation',name='section',field=models.CharField(default='api',max_length=16)),
        migrations.AddField(model_name='personaltoolconfigoperation',name='target_managed_mcp',field=models.JSONField(default=dict)),
        migrations.RunPython(migrations.RunPython.noop,require_no_agent_writes),
    ]
