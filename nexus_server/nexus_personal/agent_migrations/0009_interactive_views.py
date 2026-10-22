import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('agents', '0008_agent_wave_view')]
    operations = [
        migrations.CreateModel(name='AgentInteractiveView', fields=[
            ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('updated_at', models.DateTimeField(auto_now=True)),
            ('owner_scope', models.CharField(max_length=64, db_index=True)),
            ('definition', models.JSONField(default=dict)),
            ('state', models.JSONField(default=dict)),
            ('revision', models.PositiveIntegerField(default=1)),
            ('ticket_hash', models.CharField(max_length=64, blank=True)),
            ('ticket_expires_at', models.DateTimeField(null=True)),
            ('browser_hash', models.CharField(max_length=64, blank=True)),
            ('producer_hash', models.CharField(max_length=64)),
            ('producer_seen_at', models.DateTimeField(null=True)),
            ('expires_at', models.DateTimeField(db_index=True)),
            ('closed', models.BooleanField(default=False)),
            ('run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='agents.agentdisplayrun')),
        ]),
        migrations.CreateModel(name='AgentViewAction', fields=[
            ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('updated_at', models.DateTimeField(auto_now=True)),
            ('request_id', models.UUIDField()),
            ('name', models.CharField(max_length=64)),
            ('arguments', models.JSONField(default=dict)),
            ('status', models.CharField(max_length=16, default='pending')),
            ('lease_hash', models.CharField(max_length=64, blank=True)),
            ('deadline', models.DateTimeField()),
            ('result', models.JSONField(default=dict)),
            ('view', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='actions', to='agents.agentinteractiveview')),
        ], options={'constraints': [models.UniqueConstraint(fields=['view', 'request_id'], name='unique_view_action_request')]}),
    ]
