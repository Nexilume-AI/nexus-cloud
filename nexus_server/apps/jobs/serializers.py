from __future__ import annotations

from rest_framework import serializers

from .models import Job, JobEvent


class JobSerializer(serializers.ModelSerializer):
    tenant_id = serializers.UUIDField(source="tenant.id", read_only=True)
    project_id = serializers.UUIDField(source="project.id", read_only=True, allow_null=True)

    class Meta:
        model = Job
        fields = [
            "id",
            "tenant_id",
            "project_id",
            "job_type",
            "resource_type",
            "resource_id",
            "status",
            "celery_task_id",
            "input_json",
            "result_json",
            "error_code",
            "error_message",
            "started_at",
            "completed_at",
            "created_at",
            "updated_at",
        ]


class JobEventSerializer(serializers.ModelSerializer):
    tenant_id = serializers.UUIDField(source="tenant.id", read_only=True)
    job_id = serializers.UUIDField(source="job.id", read_only=True)

    class Meta:
        model = JobEvent
        fields = [
            "id",
            "tenant_id",
            "job_id",
            "event_type",
            "message",
            "metadata_json",
            "created_at",
            "updated_at",
        ]
