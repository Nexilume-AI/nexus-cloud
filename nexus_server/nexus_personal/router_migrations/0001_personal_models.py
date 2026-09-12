# Unreleased Personal fresh-database schema. Indexes and constraints are
# created with their tables without changing the final schema or dependencies.
# Not an in-place Enterprise-to-Personal edition conversion.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('deployments', '0002_personal_models'),
        ('providers', '0001_personal_models'),
        ('tenancy', '0001_personal_context'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Router',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('name', models.CharField(max_length=255)),
                ('router_type', models.CharField(choices=[('execution', 'Execution Router'), ('aggregation', 'Aggregation Router')], default='execution', max_length=32)),
                ('strategy', models.CharField(choices=[('cost', 'Cost'), ('quality', 'Quality'), ('custom', 'Custom'), ('manual_priority', 'Manual priority'), ('lowest_cost_pool', 'Lowest cost pool'), ('lowest_latency_pool', 'Lowest latency pool'), ('best_health_pool', 'Best health pool'), ('task_type', 'Task type')], default='cost', max_length=32)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('deployed', 'Deployed'), ('disabled', 'Disabled'), ('deleted', 'Deleted')], default='draft', max_length=32)),
                ('current_version', models.CharField(blank=True, max_length=32)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_routers', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='routers', to='tenancy.project')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='routers', to='tenancy.tenant')),
            ],
            options={
                'indexes': [models.Index(fields=['tenant', 'status'], name='routers_rou_tenant__0069cd_idx'), models.Index(fields=['tenant', 'created_at'], name='routers_rou_tenant__93ca42_idx'), models.Index(fields=['tenant', 'project', 'status'], name='routers_t_proj_status_idx')],
            },
        ),
        migrations.CreateModel(
            name='RouterModelGroupBinding',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('active', 'Active'), ('disabled', 'Disabled'), ('deleted', 'Deleted')], default='active', max_length=32)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('provider_name', models.CharField(max_length=128)),
                ('model_group_name', models.CharField(max_length=128)),
                ('enabled', models.BooleanField(default=True)),
                ('priority', models.PositiveIntegerField(default=100)),
                ('weight', models.PositiveIntegerField(default=100)),
                ('routing_hint', models.CharField(blank=True, max_length=128)),
                ('model_group', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='router_bindings', to='deployments.modelgroup')),
                ('router', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='model_group_bindings', to='routers.router')),
            ],
            options={
                'indexes': [models.Index(fields=['router', 'status', 'enabled', 'priority'], name='routers_rou_router__e8e283_idx')],
                'constraints': [models.UniqueConstraint(fields=('router', 'provider_name', 'model_group_name'), name='unique_router_model_group_binding')],
            },
        ),
        migrations.CreateModel(
            name='RouterOutput',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('active', 'Active'), ('disabled', 'Disabled'), ('deleted', 'Deleted')], default='active', max_length=32)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('model_name', models.CharField(max_length=128)),
                ('description', models.CharField(blank=True, max_length=512)),
                ('enabled', models.BooleanField(default=True)),
                ('is_default', models.BooleanField(default=False)),
                ('model_group', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='router_outputs', to='deployments.modelgroup')),
                ('router', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='outputs', to='routers.router')),
            ],
            options={
                'indexes': [models.Index(fields=['router', 'status', 'enabled', 'created_at'], name='routers_rou_router__2de8a9_idx')],
                'constraints': [models.UniqueConstraint(fields=('router', 'model_name'), name='unique_router_output_model_name')],
            },
        ),
        migrations.CreateModel(
            name='RouterChildBinding',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('active', 'Active'), ('disabled', 'Disabled'), ('deleted', 'Deleted')], default='active', max_length=32)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('exposed_model_name', models.CharField(max_length=128)),
                ('enabled', models.BooleanField(default=True)),
                ('priority', models.PositiveIntegerField(default=100)),
                ('weight', models.PositiveIntegerField(default=100)),
                ('router', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='child_bindings', to='routers.router')),
                ('child_output', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='parent_bindings', to='routers.routeroutput')),
            ],
            options={
                'indexes': [models.Index(fields=['router', 'status', 'enabled', 'exposed_model_name', 'priority'], name='routers_rou_router__762e22_idx')],
                'constraints': [models.UniqueConstraint(fields=('router', 'exposed_model_name', 'child_output'), name='unique_router_child_output_binding')],
            },
        ),
        migrations.CreateModel(
            name='RouterProviderPreference',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('active', 'Active'), ('disabled', 'Disabled'), ('deleted', 'Deleted')], default='active', max_length=32)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('preference_type', models.CharField(choices=[('own_first', 'Own First'), ('pool_first', 'Pool First'), ('specific_provider', 'Specific Provider')], default='own_first', max_length=32)),
                ('priority', models.PositiveIntegerField(default=100)),
                ('weight', models.PositiveIntegerField(default=100)),
                ('provider_account', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='router_preferences', to='providers.provideraccount')),
                ('router', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='provider_preferences', to='routers.router')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='router_provider_preferences', to='tenancy.tenant')),
            ],
            options={
                'indexes': [models.Index(fields=['tenant', 'router', 'status', 'priority'], name='routers_rou_tenant__139c3f_idx')],
            },
        ),
        migrations.CreateModel(
            name='RouterVersion',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('version', models.CharField(max_length=32)),
                ('router_file_path', models.CharField(max_length=1024)),
                ('file_name', models.CharField(blank=True, max_length=255)),
                ('file_size', models.PositiveIntegerField(default=0)),
                ('status', models.CharField(choices=[('uploaded', 'Uploaded'), ('deployed', 'Deployed')], default='uploaded', max_length=32)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_router_versions', to=settings.AUTH_USER_MODEL)),
                ('router', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='versions', to='routers.router')),
            ],
            options={
                'indexes': [models.Index(fields=['router', 'status', 'created_at'], name='routers_rou_router__c702d8_idx')],
                'constraints': [models.UniqueConstraint(fields=('router', 'version'), name='unique_router_version')],
            },
        ),
        migrations.CreateModel(
            name='RouterRuntimeInvocation',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('success', 'Success'), ('failed', 'Failed')], max_length=32)),
                ('error_code', models.CharField(blank=True, max_length=64)),
                ('error_message', models.CharField(blank=True, max_length=512)),
                ('latency_ms', models.PositiveIntegerField(default=0)),
                ('exit_code', models.IntegerField(blank=True, null=True)),
                ('request_id', models.CharField(blank=True, db_index=True, max_length=64)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='router_runtime_invocations', to=settings.AUTH_USER_MODEL)),
                ('router', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='runtime_invocations', to='routers.router')),
                ('selected_deployment', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='router_runtime_invocations', to='deployments.deployment')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='router_runtime_invocations', to='tenancy.tenant')),
                ('version', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='runtime_invocations', to='routers.routerversion')),
            ],
            options={
                'indexes': [models.Index(fields=['tenant', 'router', 'created_at'], name='routers_rou_tenant__0cb93d_idx'), models.Index(fields=['tenant', 'status', 'created_at'], name='routers_rou_tenant__121b14_idx'), models.Index(fields=['tenant', 'request_id'], name='routers_rou_tenant__157095_idx')],
            },
        ),
        migrations.CreateModel(
            name='RouterDeployment',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(choices=[('deploying', 'Deploying'), ('active', 'Active'), ('failed', 'Failed')], default='deploying', max_length=32)),
                ('endpoint_url', models.CharField(blank=True, max_length=1024)),
                ('deployed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='router_deployments', to=settings.AUTH_USER_MODEL)),
                ('router', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='deployments', to='routers.router')),
                ('version', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='deployments', to='routers.routerversion')),
            ],
            options={
                'indexes': [models.Index(fields=['router', 'status', 'created_at'], name='routers_rou_router__f9248b_idx')],
            },
        ),
    ]
