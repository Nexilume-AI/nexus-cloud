from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('personal', '0009_router_credential_computer_binding'),
        ('workspaces', '0002_workspacetoolmanagedprofile'),
    ]
    operations = [
        migrations.AddField(model_name='personaltoolconfigoperation', name='encrypted_before_config',
            field=models.TextField(blank=True)),
        migrations.AddField(model_name='personaltoolconfigoperation', name='fence_command',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='personal_tool_fences', to='workspaces.computerruntimecommand')),
        migrations.AddField(model_name='personaltoolconfigoperation', name='restore_command',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='personal_tool_restores', to='workspaces.computerruntimecommand')),
    ]
