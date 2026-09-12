"""Tool Setup uses a Router-scoped personal capability, never a full owner key."""
from django.db import models
from apps.common.schema_extension import add_field
from apps.workspaces.tool_models import WorkspaceToolManagedProfile


def register_models():
    add_field(WorkspaceToolManagedProfile, "router_credential", models.ForeignKey(
        "personal.PersonalRouterCredential", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="workspace_tool_profiles",
    ))
    add_field(WorkspaceToolManagedProfile, "agent_credential", models.ForeignKey(
        'personal.PersonalAgentCredential', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='workspace_tool_profiles',
    ))
