import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('personal', '0007_router_credentials'),
        ('workspaces', '0002_workspacetoolmanagedprofile'),
    ]
    operations = [
        migrations.CreateModel(
            name='PersonalToolConfigOperation',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('request_key', models.CharField(max_length=64)),
                ('request_digest', models.CharField(max_length=64)),
                ('state', models.CharField(default='writing', max_length=24)),
                ('active', models.BooleanField(default=True)),
                ('action', models.CharField(max_length=32)),
                ('expected_revision', models.CharField(max_length=64)),
                ('target_revision', models.CharField(max_length=64)),
                ('profile_revision', models.CharField(blank=True, max_length=64)),
                ('credential_created', models.BooleanField(default=False)),
                ('error_code', models.CharField(blank=True, max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('command', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='personal_tool_operation', to='workspaces.computerruntimecommand')),
                ('credential', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='tool_operations', to='personal.personalroutercredential')),
                ('old_credential', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='replaced_by_tool_operations', to='personal.personalroutercredential')),
                ('profile', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='personal_operations', to='workspaces.workspacetoolmanagedprofile')),
            ],
            options={'constraints': [
                models.UniqueConstraint(fields=('profile', 'request_key'), name='personal_tool_request_unique'),
                models.UniqueConstraint(condition=models.Q(('active', True)), fields=('profile',), name='personal_tool_active_unique')],
            },
        ),
    ]
