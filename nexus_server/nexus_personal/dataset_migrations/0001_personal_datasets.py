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
        ("tenancy", "0001_personal_context"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Dataset',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('active', 'Active'), ('disabled', 'Disabled'), ('deleted', 'Deleted')], default='active', max_length=32)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('name', models.CharField(max_length=255)),
                ('visibility', models.CharField(choices=[('public', 'Public'), ('private', 'Private')], default='private', max_length=32)),
                ('size_bytes', models.PositiveBigIntegerField(default=0)),
                ('file_count', models.PositiveIntegerField(default=0)),
                ('current_version', models.CharField(blank=True, max_length=32)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_datasets', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='datasets', to='tenancy.project')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='datasets', to='tenancy.tenant')),
            ],
            options={
                'indexes': [models.Index(fields=['tenant', 'visibility', 'status'], name='datasets_da_tenant__1c7cc6_idx'), models.Index(fields=['tenant', 'name', 'status'], name='datasets_da_tenant__429aa3_idx')],
            },
        ),
        migrations.CreateModel(
            name='DatasetFile',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('active', 'Active'), ('disabled', 'Disabled'), ('deleted', 'Deleted')], default='active', max_length=32)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('file_name', models.CharField(max_length=255)),
                ('storage_path', models.CharField(max_length=1024)),
                ('object_key', models.CharField(blank=True, max_length=1024)),
                ('storage_backend', models.CharField(choices=[('local', 'Local'), ('s3', 'S3-compatible')], default='local', max_length=32)),
                ('sha256', models.CharField(blank=True, db_index=True, max_length=64)),
                ('size_bytes', models.PositiveBigIntegerField(default=0)),
                ('content_type', models.CharField(blank=True, max_length=255)),
                ('metadata_json', models.JSONField(blank=True, default=dict)),
                ('dataset', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='files', to='datasets.dataset')),
                ('project', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='dataset_files', to='tenancy.project')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='dataset_files', to='tenancy.tenant')),
                ('uploaded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='uploaded_dataset_files', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'indexes': [models.Index(fields=['tenant', 'dataset', 'status', 'created_at'], name='datasets_da_tenant__4fa1b4_idx'), models.Index(fields=['tenant', 'sha256'], name='datasets_da_tenant__1dcae2_idx'), models.Index(fields=['dataset', 'status', 'created_at'], name='datasets_da_dataset_59e4f2_idx')],
            },
        ),
        migrations.CreateModel(
            name='DatasetFileIndex',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('active', 'Active'), ('disabled', 'Disabled'), ('deleted', 'Deleted')], default='active', max_length=32)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('version', models.CharField(blank=True, max_length=32)),
                ('index_status', models.CharField(choices=[('pending', 'Pending'), ('indexed', 'Indexed'), ('failed', 'Failed')], default='pending', max_length=32)),
                ('content_text', models.TextField(blank=True)),
                ('content_sha256', models.CharField(blank=True, db_index=True, max_length=64)),
                ('error_code', models.CharField(blank=True, max_length=64)),
                ('indexed_at', models.DateTimeField(blank=True, null=True)),
                ('dataset', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='file_indexes', to='datasets.dataset')),
                ('file', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='index', to='datasets.datasetfile')),
                ('project', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='dataset_file_indexes', to='tenancy.project')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='dataset_file_indexes', to='tenancy.tenant')),
            ],
            options={
                'indexes': [models.Index(fields=['tenant', 'dataset', 'index_status', 'created_at'], name='datasets_da_tenant__d0cff1_idx'), models.Index(fields=['tenant', 'content_sha256'], name='datasets_da_tenant__a608cd_idx')],
            },
        ),
        migrations.CreateModel(
            name='DatasetImportJob',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('context_project_id', models.CharField(blank=True, max_length=36)),
                ('request_key', models.CharField(max_length=128)),
                ('request_hash', models.CharField(max_length=64)),
                ('kind', models.CharField(max_length=16)),
                ('inputs', models.JSONField(default=dict)),
                ('state', models.CharField(default='queued', max_length=16)),
                ('stage', models.CharField(default='queued', max_length=32)),
                ('bytes_processed', models.PositiveBigIntegerField(default=0)),
                ('total_bytes', models.PositiveBigIntegerField(null=True)),
                ('attempts', models.PositiveIntegerField(default=0)),
                ('lease_id', models.UUIDField(null=True)),
                ('lease_expires_at', models.DateTimeField(null=True)),
                ('error_code', models.CharField(blank=True, max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('dataset', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='import_jobs', to='datasets.dataset')),
                ('file', models.OneToOneField(null=True, on_delete=django.db.models.deletion.PROTECT, to='datasets.datasetfile')),
                ('requested_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tenancy.tenant')),
            ],
            options={
                'indexes': [models.Index(fields=['state', 'lease_expires_at', 'created_at'], name='dataset_import_queue'), models.Index(fields=['dataset', 'requested_by', 'created_at'], name='dataset_import_owner')],
                'constraints': [models.UniqueConstraint(fields=('dataset', 'requested_by', 'request_key'), name='dataset_import_idempotency')],
            },
        ),
        migrations.CreateModel(
            name='DatasetQuota',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('active', 'Active'), ('disabled', 'Disabled'), ('deleted', 'Deleted')], default='active', max_length=32)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('max_size_bytes', models.PositiveBigIntegerField()),
                ('raw_value', models.CharField(max_length=64)),
                ('dataset', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='quota', to='datasets.dataset')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='dataset_quotas', to='tenancy.tenant')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='DatasetTransfer',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('kind', models.CharField(max_length=16)),
                ('identity', models.CharField(max_length=64, unique=True)),
                ('size_bytes', models.PositiveBigIntegerField()),
                ('state', models.CharField(default='active', max_length=16)),
                ('expires_at', models.DateTimeField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('storage_backend', models.CharField(blank=True, max_length=32)),
                ('object_key', models.CharField(blank=True, max_length=1024)),
                ('multipart_id', models.TextField(blank=True)),
                ('dataset', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='datasets.dataset')),
                ('file', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to='datasets.datasetfile')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tenancy.tenant')),
            ],
            options={
                'indexes': [models.Index(fields=['tenant', 'kind', 'state', 'expires_at'], name='dataset_transfer_capacity')],
            },
        ),
        migrations.CreateModel(
            name='DatasetVersion',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('active', 'Active'), ('disabled', 'Disabled'), ('deleted', 'Deleted')], default='active', max_length=32)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('version', models.CharField(max_length=32)),
                ('file_count', models.PositiveIntegerField(default=0)),
                ('size_bytes', models.PositiveBigIntegerField(default=0)),
                ('snapshot_json', models.JSONField(blank=True, default=dict)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_dataset_versions', to=settings.AUTH_USER_MODEL)),
                ('dataset', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='versions', to='datasets.dataset')),
                ('project', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='dataset_versions', to='tenancy.project')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='dataset_versions', to='tenancy.tenant')),
            ],
            options={
                'indexes': [models.Index(fields=['tenant', 'dataset', 'created_at'], name='datasets_da_tenant__b822ca_idx'), models.Index(fields=['dataset', 'created_at'], name='datasets_da_dataset_d911f5_idx')],
                'constraints': [models.UniqueConstraint(fields=('dataset', 'version'), name='unique_dataset_version')],
            },
        ),
        migrations.CreateModel(
            name='DatasetVersionEntry',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('file_id', models.UUIDField()),
                ('created_at', models.DateTimeField()),
                ('payload', models.JSONField(default=dict)),
                ('version', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='entries', to='datasets.datasetversion')),
            ],
            options={
                'indexes': [models.Index(fields=['version', 'created_at', 'id'], name='dataset_manifest_page')],
                'constraints': [models.UniqueConstraint(fields=('version', 'file_id'), name='dataset_version_entry_file')],
            },
        ),
        migrations.CreateModel(
            name='MediaAsset',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('active', 'Active'), ('disabled', 'Disabled'), ('deleted', 'Deleted')], default='active', max_length=32)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('purpose', models.CharField(choices=[('chat_input', 'Chat Input'), ('agent_attachment', 'Agent Attachment'), ('dataset_file', 'Dataset File')], default='chat_input', max_length=64)),
                ('file_name', models.CharField(max_length=255)),
                ('storage_path', models.CharField(max_length=1024)),
                ('content_type', models.CharField(max_length=255)),
                ('size_bytes', models.PositiveBigIntegerField(default=0)),
                ('sha256', models.CharField(db_index=True, max_length=64)),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
                ('owner', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='owned_media_assets', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='media_assets', to='tenancy.project')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='media_assets', to='tenancy.tenant')),
            ],
            options={
                'indexes': [models.Index(fields=['tenant', 'project', 'status', 'created_at'], name='datasets_me_tenant__198d28_idx'), models.Index(fields=['tenant', 'purpose', 'status', 'created_at'], name='datasets_me_tenant__6a4d9e_idx'), models.Index(fields=['tenant', 'sha256'], name='datasets_me_tenant__e2a568_idx')],
            },
        ),
    ]
