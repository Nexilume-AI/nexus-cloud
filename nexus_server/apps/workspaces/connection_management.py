"""Existing Computer mutation implementations, independent of credential orchestration."""
from __future__ import annotations
from typing import Any
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from apps.audit.services import log_audit
from apps.common.crypto import encrypt_secret
from .connection_core import WorkspaceError, get_workspace_connection, require_workspace_own
from .models import WorkspaceConnection, WorkspaceTerminalSession


@transaction.atomic
def update_workspace_connection(*, request, connection_id: str, data: dict[str, Any]) -> WorkspaceConnection:
    connection = get_workspace_connection(request=request, connection_id=connection_id)
    require_workspace_own(request=request, tenant=connection.tenant, action="workspace.connection.update_own", connection=connection)
    if connection.connection_type == WorkspaceConnection.TYPE_SSH and not bool(getattr(settings, "NEXUS_LEGACY_SSH_ENABLED", False)):
        from .computer_runtime import LegacySSHDisabled

        raise LegacySSHDisabled()
    changed: list[str] = []
    for field in ["name", "metadata", "workspace_root"]:
        if field in data:
            setattr(connection, field, data[field])
            changed.append(field)
    if "private_key" in data:
        connection.encrypted_private_key = encrypt_secret(data.get("private_key", ""))
        changed.append("encrypted_private_key")
    if "password" in data:
        connection.encrypted_password = encrypt_secret(data.get("password", ""))
        changed.append("encrypted_password")
    if changed:
        connection.save(update_fields=changed + ["updated_at"])
    log_audit(request=request, action="workspaces.connection.update", actor=request.user, resource_type="workspace_connection", resource_id=connection.id, metadata={"fields": changed})
    return connection


@transaction.atomic
def delete_workspace_connection(*, request, connection_id: str) -> WorkspaceConnection:
    connection = get_workspace_connection(request=request, connection_id=connection_id)
    require_workspace_own(request=request, tenant=connection.tenant, action="workspace.connection.delete_own", connection=connection)
    active_session_exists = WorkspaceTerminalSession.objects.filter(
        connection=connection,
        status__in=[WorkspaceTerminalSession.STATUS_CREATED, WorkspaceTerminalSession.STATUS_ACTIVE],
    ).exists()
    if active_session_exists:
        raise WorkspaceError("Close active terminal sessions before deleting the connection.")
    if connection.connection_type == WorkspaceConnection.TYPE_RUNTIME:
        from .computer_runtime import _discard_runtime_uploads, publish_device_wakeup
        from .models import ComputerRuntimeCommand

        try:
            device = connection.runtime_device
        except Exception:
            device = None
        if device is not None:
            now = timezone.now()
            device.revoked_at = now
            device.generation += 1
            device.save(update_fields=["revoked_at", "generation", "updated_at"])
            commands = list(device.commands.select_for_update().filter(status__in=[
                ComputerRuntimeCommand.STATUS_QUEUED,
                ComputerRuntimeCommand.STATUS_DISPATCHED,
                ComputerRuntimeCommand.STATUS_RUNNING,
            ]))
            for command in commands:
                command.status = ComputerRuntimeCommand.STATUS_CANCELED
                command.error_code = "COMPUTER_RUNTIME_REVOKED"
                command.error_message = "Computer Runtime was deleted."
                command.completed_at = now
                command.save(update_fields=["status", "error_code", "error_message", "completed_at", "updated_at"])
                _discard_runtime_uploads(command)
            transaction.on_commit(lambda: publish_device_wakeup(str(device.id)))
    connection.encrypted_private_key = ""
    connection.encrypted_password = ""
    if connection.connection_type == WorkspaceConnection.TYPE_SSH:
        connection.ssh_host = ""
        connection.ssh_user = ""
    connection.save(update_fields=[
        "encrypted_private_key",
        "encrypted_password",
        "ssh_host",
        "ssh_user",
        "updated_at",
    ])
    connection.delete()
    log_audit(request=request, action="workspaces.connection.delete", actor=request.user, resource_type="workspace_connection", resource_id=connection.id)
    return connection
