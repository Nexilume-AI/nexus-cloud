# Unreleased Personal initial schema. Fold dependency-ready fields and indexes
# into table creation; retain cross-app cycles in the second migration.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('agents', '0001_personal_runtime'),
        ('mobile', '0001_personal_runtime'),
        ('tenancy', '0001_personal_context'),
        ('workspaces', '0001_personal_runtime'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='agentbrowsersession',
            name='connection',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='agent_browser_sessions', to='workspaces.workspaceconnection'),
        ),
        migrations.AddField(
            model_name='agentcomputerbinding',
            name='connection',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='agent_bindings', to='workspaces.workspaceconnection'),
        ),
        migrations.CreateModel(
            name='AgentTaskExecution',
            fields=[
                ('task', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, primary_key=True, related_name='execution', serialize=False, to='agents.agentexecutiontask')),
                ('encrypted_payload', models.TextField(blank=True)),
                ('state', models.CharField(db_index=True, default='queued', max_length=32)),
                ('lease_id', models.UUIDField(blank=True, null=True)),
                ('lease_expires_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('heartbeat_at', models.DateTimeField(blank=True, null=True)),
                ('cancel_requested_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.AddField(
            model_name='agentmobilebinding',
            name='device',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='agent_bindings', to='mobile.mobiledevice'),
        ),
        migrations.AddField(
            model_name='agentmobilelease',
            name='device',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='agent_run_leases', to='mobile.mobiledevice'),
        ),
        migrations.AddField(
            model_name='agentruncomputerattachment',
            name='terminal_session',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='workspaces.workspaceterminalsession'),
        ),
        migrations.AddField(
            model_name='agentruntimedeployment',
            name='workspace_connection',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='agent_runtime_deployments', to='workspaces.workspaceconnection'),
        ),
        migrations.AddIndex(
            model_name='agentcomputerbinding',
            index=models.Index(fields=['tenant', 'caller_subject_hash', 'status'], name='agent_comp_tenant_subj_idx'),
        ),
        migrations.AddIndex(
            model_name='agentcomputerbinding',
            index=models.Index(fields=['agent', 'caller_subject_hash', 'status'], name='agent_comp_agent_subj_idx'),
        ),
        migrations.AddConstraint(
            model_name='agentcomputerbinding',
            constraint=models.UniqueConstraint(condition=models.Q(('is_default', True), ('status', 'active')), fields=('tenant', 'agent', 'caller_subject_hash'), name='unique_default_agent_computer_binding'),
        ),
        migrations.AddIndex(
            model_name='agentbrowsersession',
            index=models.Index(fields=['connection', 'status', 'started_at'], name='agent_browser_connection_idx'),
        ),
        migrations.AddIndex(
            model_name='agentmobilebinding',
            index=models.Index(fields=['tenant', 'caller_subject_hash', 'status'], name='agent_mobile_tenant_subj_idx'),
        ),
        migrations.AddIndex(
            model_name='agentmobilebinding',
            index=models.Index(fields=['agent', 'caller_subject_hash', 'status'], name='agent_mobile_agent_subj_idx'),
        ),
        migrations.AddConstraint(
            model_name='agentmobilebinding',
            constraint=models.UniqueConstraint(condition=models.Q(('is_default', True), ('project__isnull', True), ('status', 'active')), fields=('tenant', 'agent', 'caller_subject_hash'), name='uniq_agent_mobile_tenant_def'),
        ),
        migrations.AddConstraint(
            model_name='agentmobilebinding',
            constraint=models.UniqueConstraint(condition=models.Q(('is_default', True), ('project__isnull', False), ('status', 'active')), fields=('tenant', 'project', 'agent', 'caller_subject_hash'), name='uniq_agent_mobile_proj_def'),
        ),
        migrations.AddConstraint(
            model_name='agentmobilelease',
            constraint=models.UniqueConstraint(condition=models.Q(('status', 'active')), fields=('device',), name='uniq_active_agent_mobile_lease'),
        ),
        migrations.AddConstraint(
            model_name='agentruncomputerattachment',
            constraint=models.UniqueConstraint(fields=('run', 'revision'), name='unique_run_computer_revision'),
        ),
        migrations.AddIndex(
            model_name='agentruntimedeployment',
            index=models.Index(fields=['tenant', 'agent', 'status'], name='agents_agen_tenant__f5e856_idx'),
        ),
        migrations.AddIndex(
            model_name='agentruntimedeployment',
            index=models.Index(fields=['tenant', 'health_status'], name='agents_agen_tenant__57923a_idx'),
        ),
        migrations.AddConstraint(
            model_name='agentruntimedeployment',
            constraint=models.UniqueConstraint(fields=('tenant', 'agent', 'env'), name='unique_agent_runtime_env'),
        ),
        migrations.AddConstraint(
            model_name='agentruntimedeployment',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('edge_registration__isnull', True), ('image__isnull', False), ('runtime_kind', 'docker')), models.Q(('edge_registration__isnull', False), ('image__isnull', True), ('runtime_kind', 'openwrt_ipv6')), models.Q(('edge_registration__isnull', False), ('image__isnull', True), ('runtime_kind', 'openwrt_relay')), _connector='OR'), name='agent_runtime_kind_target_valid'),
        ),
    ]
