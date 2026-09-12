import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def require_drained_capabilities(apps, schema_editor):
    usage = apps.get_model('personal','PersonalInvocationUsage')
    if usage.objects.using(schema_editor.connection.alias).filter(
            agent_credential_id__isnull=False,finished_at__isnull=True).exists():
        raise RuntimeError('Finish or terminate credential-bound Agent invocations before downgrading; their authority cannot be dropped.')


class Migration(migrations.Migration):
    dependencies = [
        ('personal','0010_tool_config_recovery'),
        ('tenancy','0001_personal_context'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AddField(model_name='personalinvocationusage',name='agent_credential_id',
            field=models.UUIDField(blank=True,db_index=True,null=True)),
        migrations.CreateModel(name='PersonalAgentCredential',fields=[
            ('id',models.UUIDField(default=uuid.uuid4,editable=False,primary_key=True,serialize=False)),
            ('token_hash',models.CharField(max_length=64,unique=True)),
            ('agent_ids',models.JSONField(default=list)),
            ('tool_connection_id',models.UUIDField(db_index=True)),
            ('tool_device_id',models.UUIDField(db_index=True)),
            ('tool_profile_id',models.UUIDField(db_index=True)),
            ('created_at',models.DateTimeField(auto_now_add=True)),
            ('expires_at',models.DateTimeField()),
            ('revoked_at',models.DateTimeField(blank=True,null=True)),
            ('last_used_at',models.DateTimeField(blank=True,null=True)),
            ('owner',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,to=settings.AUTH_USER_MODEL)),
            ('project',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,to='tenancy.project')),
            ('tenant',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,to='tenancy.tenant')),
        ]),
        migrations.RunPython(migrations.RunPython.noop,require_drained_capabilities),
    ]
