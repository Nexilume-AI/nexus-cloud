# Unreleased Personal initial schema: create dependency-ready indexes and
# constraints with their tables instead of reloading the related model graph.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('agents', '0001_personal_runtime'),
        ('tenancy', '0001_personal_context'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='WorkspaceConnection',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('active', 'Active'), ('disabled', 'Disabled'), ('deleted', 'Deleted')], default='active', max_length=32)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('owner_subject_type', models.CharField(blank=True, max_length=32)),
                ('owner_subject_hash', models.CharField(blank=True, db_index=True, max_length=64)),
                ('workspace_root', models.CharField(default='~/.nexus', max_length=1024)),
                ('name', models.CharField(max_length=128)),
                ('connection_type', models.CharField(choices=[('runtime', 'Nexus Computer Runtime'), ('ssh', 'Legacy SSH')], default='ssh', max_length=32)),
                ('ssh_host', models.CharField(max_length=255)),
                ('ssh_port', models.PositiveIntegerField(default=22)),
                ('ssh_user', models.CharField(max_length=128)),
                ('auth_mode', models.CharField(choices=[('private_key', 'Private Key'), ('password', 'Password')], default='private_key', max_length=32)),
                ('encrypted_private_key', models.TextField(blank=True)),
                ('encrypted_password', models.TextField(blank=True)),
                ('last_test_status', models.CharField(choices=[('unknown', 'Unknown'), ('succeeded', 'Succeeded'), ('failed', 'Failed')], default='unknown', max_length=32)),
                ('last_test_error', models.CharField(blank=True, max_length=1024)),
                ('last_test_at', models.DateTimeField(blank=True, null=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_workspace_connections', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='workspace_connections', to='tenancy.project')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='workspace_connections', to='tenancy.tenant')),
            ],
            options={
                'indexes': [
                    models.Index(fields=['tenant', 'status', 'created_at'], name='workspaces__tenant__832929_idx'),
                    models.Index(fields=['tenant', 'project', 'status'], name='workspaces__tenant__e97197_idx'),
                    models.Index(fields=['tenant', 'ssh_host', 'ssh_user'], name='workspaces__tenant__9ec14b_idx'),
                ],
                'constraints': [
                    models.UniqueConstraint(condition=models.Q(('status', 'active')), fields=('tenant', 'owner_subject_hash', 'name'), name='unique_active_owned_workspace_name'),
                ],
            },
        ),
        migrations.CreateModel(
            name='ComputerRuntimeEnrollment',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('owner_subject_type', models.CharField(max_length=32)),
                ('owner_subject_hash', models.CharField(db_index=True, max_length=64)),
                ('token_hash', models.CharField(max_length=64, unique=True)),
                ('expires_at', models.DateTimeField()),
                ('used_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_computer_runtime_enrollments', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='computer_runtime_enrollments', to='tenancy.project')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='computer_runtime_enrollments', to='tenancy.tenant')),
                ('connection', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='runtime_enrollment', to='workspaces.workspaceconnection')),
            ],
            options={
                'indexes': [
                    models.Index(fields=['tenant', 'owner_subject_hash', 'expires_at'], name='computer_enroll_owner_exp_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='ComputerRuntimeDevice',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('public_key_pem', models.TextField()),
                ('public_key_fingerprint', models.CharField(max_length=64, unique=True)),
                ('platform', models.CharField(choices=[('windows', 'Windows'), ('linux', 'Linux'), ('macos', 'macOS')], max_length=16)),
                ('protocol_version', models.PositiveSmallIntegerField(default=1)),
                ('capabilities', models.JSONField(blank=True, default=dict)),
                ('facts', models.JSONField(blank=True, default=dict)),
                ('generation', models.PositiveBigIntegerField(default=0)),
                ('last_server_sequence', models.PositiveBigIntegerField(default=0)),
                ('last_client_sequence', models.PositiveBigIntegerField(default=0)),
                ('auth_challenge_hash', models.CharField(blank=True, max_length=64)),
                ('auth_challenge_expires_at', models.DateTimeField(blank=True, null=True)),
                ('connect_ticket_hash', models.CharField(blank=True, max_length=64)),
                ('connect_ticket_expires_at', models.DateTimeField(blank=True, null=True)),
                ('last_seen_at', models.DateTimeField(blank=True, null=True)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('connection', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='runtime_device', to='workspaces.workspaceconnection')),
            ],
            options={
                'indexes': [
                    models.Index(fields=['last_seen_at', 'revoked_at'], name='computer_runtime_presence_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='ComputerRuntimeCommand',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('display_run_id', models.UUIDField(blank=True, db_index=True, null=True)),
                ('caller_subject_hash', models.CharField(blank=True, db_index=True, max_length=64)),
                ('required_scope', models.CharField(max_length=64)),
                ('operation', models.CharField(max_length=96)),
                ('idempotency_key', models.CharField(blank=True, max_length=128)),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('dispatched', 'Dispatched'), ('running', 'Running'), ('succeeded', 'Succeeded'), ('failed', 'Failed'), ('canceled', 'Canceled'), ('expired', 'Expired')], default='queued', max_length=16)),
                ('encrypted_payload', models.TextField()),
                ('payload_summary', models.JSONField(blank=True, default=dict)),
                ('result', models.JSONField(blank=True, default=dict)),
                ('encrypted_result', models.TextField(blank=True)),
                ('error_code', models.CharField(blank=True, max_length=64)),
                ('error_message', models.CharField(blank=True, max_length=1024)),
                ('server_sequence', models.PositiveBigIntegerField(default=0)),
                ('expires_at', models.DateTimeField()),
                ('dispatched_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('device', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='commands', to='workspaces.computerruntimedevice')),
                ('connection', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='runtime_commands', to='workspaces.workspaceconnection')),
            ],
            options={
                'indexes': [
                    models.Index(fields=['device', 'status', 'created_at'], name='computer_command_queue_idx'),
                    models.Index(fields=['device', 'expires_at'], name='computer_command_expiry_idx'),
                ],
                'constraints': [
                    models.UniqueConstraint(condition=models.Q(('idempotency_key', ''), _negated=True), fields=('device', 'idempotency_key'), name='unique_computer_runtime_idempotency'),
                ],
            },
        ),
        migrations.CreateModel(
            name='WorkspaceTerminalSession',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(choices=[('created', 'Created'), ('active', 'Active'), ('closed', 'Closed'), ('failed', 'Failed'), ('deleted', 'Deleted')], default='created', max_length=32)),
                ('session_kind', models.CharField(choices=[('user', 'User'), ('agent_run', 'Agent run')], default='user', max_length=32)),
                ('caller_subject_hash', models.CharField(blank=True, db_index=True, max_length=64)),
                ('authorized_root', models.CharField(blank=True, max_length=1024)),
                ('output_root', models.CharField(blank=True, max_length=1024)),
                ('viewer_mode', models.CharField(default='read_only', max_length=32)),
                ('shell', models.CharField(choices=[('auto', 'Auto'), ('powershell', 'PowerShell'), ('sh', 'sh'), ('bash', 'bash')], default='auto', max_length=32)),
                ('cols', models.PositiveIntegerField(default=100)),
                ('rows', models.PositiveIntegerField(default=30)),
                ('last_error', models.CharField(blank=True, max_length=1024)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('ended_at', models.DateTimeField(blank=True, null=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('computer_binding', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='terminal_sessions', to='agents.agentcomputerbinding')),
                ('connection', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='terminal_sessions', to='workspaces.workspaceconnection')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_workspace_terminal_sessions', to=settings.AUTH_USER_MODEL)),
                ('display_run', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='terminal_session', to='agents.agentdisplayrun')),
                ('project', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='workspace_terminal_sessions', to='tenancy.project')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='workspace_terminal_sessions', to='tenancy.tenant')),
            ],
            options={
                'indexes': [
                    models.Index(fields=['tenant', 'status', 'created_at'], name='workspaces__tenant__6779b1_idx'),
                    models.Index(fields=['tenant', 'project', 'status'], name='workspaces__tenant__c38e1b_idx'),
                    models.Index(fields=['connection', 'status'], name='workspaces__connect_f5424e_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='WorkspaceTerminalTranscript',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('seq', models.PositiveIntegerField()),
                ('kind', models.CharField(max_length=16)),
                ('command_id', models.CharField(blank=True, max_length=64)),
                ('data', models.TextField(blank=True)),
                ('exit_code', models.IntegerField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='transcript', to='workspaces.workspaceterminalsession')),
            ],
            options={
                'indexes': [
                    models.Index(fields=['session', 'seq'], name='workspaces_transcript_seq_idx'),
                ],
                'constraints': [
                    models.UniqueConstraint(fields=('session', 'seq'), name='unique_workspace_terminal_seq'),
                ],
            },
        ),
    ]
