from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import BaseModel
from apps.tenancy.models import Project, Tenant


class Job(BaseModel):
    STATUS_QUEUED = "queued"
    STATUS_RUNNING = "running"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_FAILED = "failed"
    STATUS_CANCELED = "canceled"
    STATUS_CHOICES = (
        (STATUS_QUEUED, "Queued"),
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCEEDED, "Succeeded"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELED, "Canceled"),
    )

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="jobs")
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name="jobs")
    job_type = models.CharField(max_length=128)
    resource_type = models.CharField(max_length=64)
    resource_id = models.CharField(max_length=128, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    celery_task_id = models.CharField(max_length=128, blank=True)
    input_json = models.JSONField(default=dict, blank=True)
    result_json = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_jobs",
    )

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "status", "created_at"]),
            models.Index(fields=["tenant", "job_type", "created_at"]),
            models.Index(fields=["tenant", "resource_type", "resource_id"]),
        ]


class JobEvent(BaseModel):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="job_events")
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=64)
    message = models.CharField(max_length=512, blank=True)
    metadata_json = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "job", "created_at"]),
            models.Index(fields=["tenant", "event_type", "created_at"]),
        ]
