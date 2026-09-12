from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('personal','0011_agent_mcp_credentials'),
        ('workspaces','0002_workspacetoolmanagedprofile'),
    ]
    operations = [
        migrations.AddField(model_name='workspacetoolmanagedprofile',name='agent_credential',
            field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,
                related_name='workspace_tool_profiles',to='personal.personalagentcredential')),
    ]
