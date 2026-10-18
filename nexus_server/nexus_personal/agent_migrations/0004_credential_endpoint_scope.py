from django.db import migrations, models
import django.db.models.deletion


def backfill(apps,schema_editor):
    grants=apps.get_model('agents','AgentCredentialAuthorization')
    for provider,url in {'nexus':'https://cloud.nexilume.com/api/v1/openai/v1','openai':'https://api.openai.com/v1','anthropic':'https://api.anthropic.com'}.items():
        grants.objects.filter(provider=provider,base_url='').update(base_url=url)


class Migration(migrations.Migration):
    dependencies=[('agents','0003_provider_authorization')]
    operations=[
        migrations.AddField(model_name='agentcredentialauthorization',name='base_url',field=models.CharField(max_length=2048,blank=True)),
        migrations.AddField(model_name='agentcredentialauthorization',name='model_name',field=models.CharField(max_length=128,blank=True)),
        migrations.AddField(model_name='agentcredentialauthorization',name='source_grant',field=models.ForeignKey(null=True,blank=True,to='agents.agentcredentialauthorization',on_delete=django.db.models.deletion.SET_NULL,related_name='derived_grants')),
        migrations.RunPython(backfill,migrations.RunPython.noop),
    ]
