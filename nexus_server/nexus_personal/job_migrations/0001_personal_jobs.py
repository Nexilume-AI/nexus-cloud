# Unreleased Personal initial schema: retain all Job indexes at table creation.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('tenancy', '0001_personal_context'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Job',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('job_type', models.CharField(max_length=128)),
                ('resource_type', models.CharField(max_length=64)),
                ('resource_id', models.CharField(blank=True, max_length=128)),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('running', 'Running'), ('succeeded', 'Succeeded'), ('failed', 'Failed'), ('canceled', 'Canceled')], default='queued', max_length=32)),
                ('celery_task_id', models.CharField(blank=True, max_length=128)),
                ('input_json', models.JSONField(blank=True, default=dict)),
                ('result_json', models.JSONField(blank=True, default=dict)),
                ('error_code', models.CharField(blank=True, max_length=64)),
                ('error_message', models.TextField(blank=True)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_jobs', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='jobs', to='tenancy.project')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='jobs', to='tenancy.tenant')),
            ],
            options={
                'indexes': [
                    models.Index(fields=['tenant', 'status', 'created_at'], name='jobs_job_tenant__eb198b_idx'),
                    models.Index(fields=['tenant', 'job_type', 'created_at'], name='jobs_job_tenant__58d3c9_idx'),
                    models.Index(fields=['tenant', 'resource_type', 'resource_id'], name='jobs_job_tenant__ecb2bf_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='JobEvent',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('event_type', models.CharField(max_length=64)),
                ('message', models.CharField(blank=True, max_length=512)),
                ('metadata_json', models.JSONField(blank=True, default=dict)),
                ('job', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='jobs.job')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='job_events', to='tenancy.tenant')),
            ],
            options={
                'indexes': [
                    models.Index(fields=['tenant', 'job', 'created_at'], name='jobs_jobeve_tenant__a5fe3d_idx'),
                    models.Index(fields=['tenant', 'event_type', 'created_at'], name='jobs_jobeve_tenant__c613be_idx'),
                ],
            },
        ),
    ]
