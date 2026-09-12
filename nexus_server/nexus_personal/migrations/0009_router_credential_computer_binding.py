"""Bind only proven Tool Setup-issued keys; never adopt ordinary exported keys."""
import uuid
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


def bind_existing_tool_keys(apps, schema_editor):
    keys = apps.get_model('personal', 'PersonalRouterCredential')
    operations = apps.get_model('personal', 'PersonalToolConfigOperation')
    alias = schema_editor.connection.alias
    proven = operations.objects.using(alias).filter(credential_created=True, credential_id__isnull=False)
    for credential_id in proven.values_list('credential_id', flat=True).distinct().iterator():
        rows = list(proven.filter(credential_id=credential_id).values(
            'profile_id', 'profile__connection_id', 'command__device_id',
            'command__device__connection_id', 'command__device__revoked_at'))
        identities = {(row['profile_id'], row['profile__connection_id'], row['command__device_id']) for row in rows}
        safe = len(identities) == 1 and all(row['command__device_id'] and
            row['command__device__connection_id'] == row['profile__connection_id'] and
            row['command__device__revoked_at'] is None for row in rows)
        profile_id, connection_id, device_id = next(iter(identities))
        update = {'issued_for':'tool_setup', 'tool_profile_id':profile_id,
            'tool_connection_id':connection_id, 'tool_device_id':device_id or uuid.UUID(int=0)}
        # Missing/ambiguous historical receipt is not proof of the current pairing.
        target = keys.objects.using(alias).filter(pk=credential_id, issued_for='exported')
        changed = target.update(**update)
        if changed and not safe:
            keys.objects.using(alias).filter(pk=credential_id, revoked_at__isnull=True).update(revoked_at=timezone.now())


def revoke_before_unbinding(apps, schema_editor):
    # A downgrade must not turn a device-scoped key into an ordinary export.
    keys = apps.get_model('personal', 'PersonalRouterCredential')
    keys.objects.using(schema_editor.connection.alias).filter(issued_for='tool_setup',
        revoked_at__isnull=True).update(revoked_at=timezone.now())


class Migration(migrations.Migration):
    dependencies = [
        ('personal','0008_personaltoolconfigoperation'),
        ('routers','0001_personal_models'),
        ('tenancy','0001_personal_context'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AddField(model_name='personalroutercredential',name='issued_for',
            field=models.CharField(choices=[('exported','Exported'),('tool_setup','Tool Setup')],default='exported',max_length=16)),
        migrations.AddField(model_name='personalroutercredential',name='tool_connection_id',field=models.UUIDField(blank=True,db_index=True,null=True)),
        migrations.AddField(model_name='personalroutercredential',name='tool_device_id',field=models.UUIDField(blank=True,db_index=True,null=True)),
        migrations.AddField(model_name='personalroutercredential',name='tool_profile_id',field=models.UUIDField(blank=True,db_index=True,null=True)),
        migrations.RunPython(bind_existing_tool_keys, revoke_before_unbinding),
        migrations.AddConstraint(model_name='personalroutercredential',constraint=models.CheckConstraint(
            condition=(models.Q(issued_for='exported',tool_connection_id__isnull=True,tool_device_id__isnull=True,tool_profile_id__isnull=True) |
                models.Q(issued_for='tool_setup',tool_connection_id__isnull=False,tool_device_id__isnull=False,tool_profile_id__isnull=False)),
            name='personal_router_key_origin_valid')),
    ]
