"""Shared Tool Setup integration schema, composed separately from device execution.

This stays core, not Enterprise-only. Credential relations are contributed by
an explicit host; the original Enterprise database schema remains unchanged.
"""
from __future__ import annotations
from django.conf import settings
from django.db import models
from apps.common.models import SoftDeleteModel
from apps.tenancy.models import Tenant, Project
from .models import WorkspaceConnection


class WorkspaceToolManagedProfile(SoftDeleteModel):
    """Non-secret ownership metadata for a Nexus-managed remote tool profile."""

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="workspace_tool_profiles")
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name="workspace_tool_profiles")
    connection = models.ForeignKey(WorkspaceConnection, on_delete=models.CASCADE, related_name="tool_profiles")
    tool = models.CharField(max_length=32, default="codex")
    profile = models.CharField(max_length=64, default="nexus")
    provider_runtime = models.ForeignKey(
        "providers.ProviderRuntimeAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="workspace_tool_profiles",
    )
    router = models.ForeignKey(
        "routers.Router",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="workspace_tool_profiles",
    )
    managed_mcp_servers = models.JSONField(default=dict, blank=True)
    config_revision = models.CharField(max_length=64, blank=True)
    last_backup_path = models.CharField(max_length=1024, blank=True)
    last_backup_at = models.DateTimeField(null=True, blank=True)
    last_applied_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_workspace_tool_profiles",
    )

    class Meta:
        app_label = "workspaces"
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "tool", "profile"],
                condition=models.Q(status=SoftDeleteModel.STATUS_ACTIVE),
                name="unique_active_workspace_tool_profile",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "project", "status"]),
            models.Index(fields=["connection", "tool", "profile"]),
        ]


from apps.common.schema_extension import schema_extension
schema_extension("NEXUS_WORKSPACE_TOOL_MODEL_EXTENSION").register_models()
