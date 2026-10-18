import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('agents', '0005_credential_router_scope')]
    operations = [
        migrations.CreateModel(name='AgentBackgroundJob', fields=[
            ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
            ('request_id', models.UUIDField()), ('push_secret', models.TextField()),
            ('expires_at', models.DateTimeField()), ('last_seq', models.PositiveIntegerField(default=0)),
            ('byte_count', models.PositiveIntegerField(default=0)), ('state', models.JSONField(default=dict)),
            ('artifacts', models.JSONField(default=dict)), ('cancel_requested', models.BooleanField(default=False)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('authorization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='agents.agentcredentialauthorization')),
        ]),
        migrations.AddConstraint(model_name='agentbackgroundjob', constraint=models.UniqueConstraint(fields=('authorization','request_id'),name='agent_bg_request_unique')),
        migrations.CreateModel(name='AgentBackgroundEvent', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('seq', models.PositiveIntegerField()), ('payload', models.JSONField()),
            ('job', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events',to='agents.agentbackgroundjob')),
        ], options={'ordering':['seq']}),
        migrations.AddConstraint(model_name='agentbackgroundevent', constraint=models.UniqueConstraint(fields=('job','seq'),name='agent_bg_event_unique')),
    ]
