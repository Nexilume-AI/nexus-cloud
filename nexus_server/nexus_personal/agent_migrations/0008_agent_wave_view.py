import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('agents', '0007_mcp_config_preauthorization')]
    operations = [migrations.CreateModel(name='AgentWaveView', fields=[
        ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('updated_at', models.DateTimeField(auto_now=True)),
        ('owner_scope', models.CharField(max_length=64, db_index=True)),
        ('file_ids', models.JSONField(default=list)),
        ('snapshot', models.JSONField(default=dict)),
        ('revision', models.PositiveIntegerField(default=1)),
        ('browser_state', models.JSONField(default=dict)),
        ('ticket_hash', models.CharField(max_length=64, blank=True)),
        ('ticket_expires_at', models.DateTimeField(null=True)),
        ('browser_hash', models.CharField(max_length=64, blank=True)),
        ('expires_at', models.DateTimeField(db_index=True)),
        ('closed', models.BooleanField(default=False)),
        ('run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='agents.agentdisplayrun')),
    ])]
