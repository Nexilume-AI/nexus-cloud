"""Shared Runtime selection; legacy SSH is an explicit compatibility opt-in."""
from django.conf import settings
from .models import WorkspaceConnection
from .runtime_runner import ComputerRuntimeWorkspaceRunner


def workspace_runner_for(connection):
    if connection.connection_type == WorkspaceConnection.TYPE_RUNTIME:
        return ComputerRuntimeWorkspaceRunner()
    if not bool(getattr(settings, "NEXUS_LEGACY_SSH_ENABLED", False)):
        from .computer_runtime import LegacySSHDisabled
        raise LegacySSHDisabled()
    from .services import workspace_runner
    return workspace_runner()


def workspace_runner_name_for(connection):
    if connection.connection_type == WorkspaceConnection.TYPE_RUNTIME:
        return "computer_runtime"
    return str(getattr(settings, "NEXUS_WORKSPACE_SSH_RUNNER", "fake")).lower()
