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
            name='MobileDevice',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('active', 'Active'), ('disabled', 'Disabled'), ('deleted', 'Deleted')], default='active', max_length=32)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('name', models.CharField(max_length=128)),
                ('platform', models.CharField(choices=[('android', 'Android')], default='android', max_length=32)),
                ('device_identifier', models.CharField(blank=True, max_length=255)),
                ('token_prefix', models.CharField(db_index=True, max_length=32)),
                ('token_hash', models.CharField(max_length=64, unique=True)),
                ('approval_mode', models.CharField(choices=[('manual', 'Manual'), ('confirm_high_risk', 'Confirm high risk'), ('auto', 'Auto')], default='confirm_high_risk', max_length=32)),
                ('online_status', models.CharField(choices=[('unknown', 'Unknown'), ('online', 'Online'), ('offline', 'Offline')], default='unknown', max_length=32)),
                ('capabilities', models.JSONField(blank=True, default=dict)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('current_package', models.CharField(blank=True, max_length=255)),
                ('current_activity', models.CharField(blank=True, max_length=255)),
                ('last_observation', models.JSONField(blank=True, default=dict)),
                ('last_screenshot', models.BinaryField(blank=True, null=True)),
                ('last_screenshot_content_type', models.CharField(blank=True, max_length=32)),
                ('last_screenshot_captured_at', models.DateTimeField(blank=True, null=True)),
                ('last_seen_at', models.DateTimeField(blank=True, null=True)),
                ('owner_subject_hash', models.CharField(blank=True, db_index=True, max_length=64)),
                ('owner_principal_type', models.CharField(blank=True, max_length=32)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_mobile_devices', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='mobile_devices', to='tenancy.project')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='mobile_devices', to='tenancy.tenant')),
            ],
            options={
                'indexes': [
                    models.Index(fields=['tenant', 'status', 'created_at'], name='mobile_mobi_tenant__7a9c76_idx'),
                    models.Index(fields=['tenant', 'project', 'status'], name='mobile_mobi_tenant__ee42d5_idx'),
                    models.Index(fields=['tenant', 'online_status', 'last_seen_at'], name='mobile_mobi_tenant__72b7ca_idx'),
                ],
                'constraints': [
                    models.UniqueConstraint(fields=('tenant', 'owner_subject_hash', 'name', 'status'), name='unique_caller_mobile_device_name_status'),
                ],
            },
        ),
        migrations.CreateModel(
            name='MobileCommand',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('caller_subject_hash', models.CharField(blank=True, db_index=True, max_length=64)),
                ('client_request_id', models.UUIDField(blank=True, editable=False, null=True)),
                ('action', models.CharField(choices=[('observe', 'Observe'), ('tap_text', 'Tap text'), ('tap_coordinates', 'Tap coordinates'), ('type_text', 'Type text'), ('swipe', 'Swipe'), ('press_back', 'Press back'), ('open_app', 'Open app'), ('wait_for_state', 'Wait for state'), ('capture_screen', 'Capture screen')], max_length=64)),
                ('arguments', models.JSONField(blank=True, default=dict)),
                ('status', models.CharField(choices=[('pending_approval', 'Pending approval'), ('queued', 'Queued'), ('running', 'Running'), ('succeeded', 'Succeeded'), ('failed', 'Failed'), ('rejected', 'Rejected'), ('canceled', 'Canceled'), ('deleted', 'Deleted')], default='queued', max_length=32)),
                ('risk_level', models.CharField(choices=[('low', 'Low'), ('medium', 'Medium'), ('high', 'High')], default='medium', max_length=32)),
                ('requires_approval', models.BooleanField(default=False)),
                ('result', models.JSONField(blank=True, default=dict)),
                ('screenshot', models.BinaryField(blank=True, null=True)),
                ('screenshot_content_type', models.CharField(blank=True, max_length=32)),
                ('error', models.CharField(blank=True, max_length=1024)),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('dispatched_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
                ('approved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='approved_mobile_commands', to=settings.AUTH_USER_MODEL)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_mobile_commands', to=settings.AUTH_USER_MODEL)),
                ('display_run', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='mobile_commands', to='agents.agentdisplayrun')),
                ('mobile_binding', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='commands', to='agents.agentmobilebinding')),
                ('project', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='mobile_commands', to='tenancy.project')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='mobile_commands', to='tenancy.tenant')),
                ('device', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='commands', to='mobile.mobiledevice')),
            ],
            options={
                'indexes': [
                    models.Index(fields=['tenant', 'status', 'created_at'], name='mobile_mobi_tenant__d61b62_idx'),
                    models.Index(fields=['device', 'status', 'created_at'], name='mobile_mobi_device__817235_idx'),
                    models.Index(fields=['device', 'expires_at'], name='mobile_mobi_device__704a4f_idx'),
                ],
                'constraints': [
                    models.UniqueConstraint(condition=models.Q(('client_request_id__isnull', False)), fields=('display_run', 'client_request_id'), name='uniq_mobile_command_run_client_request'),
                ],
            },
        ),
    ]
