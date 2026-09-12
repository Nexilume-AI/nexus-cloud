"""Explicit recovery of unconfirmed personal Tool Setup operations.

The SDK barrier advances a durable per-device fence under the config lock.
Only its bounded snapshot authorizes settlement, preserving local changes or
compensation. Never infer no external effect solely from a Cloud timeout.
"""
import hashlib
import json
import tomllib
import uuid
from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions
from cryptography.fernet import InvalidToken
from apps.common.crypto import decrypt_secret
from apps.workspaces.connection_core import WorkspaceConfigConflict, WorkspaceNotFound
from apps.workspaces.computer_runtime import wait_for_runtime_command
from apps.workspaces.tool_runtime import enqueue_tool_config_fenced
from apps.workspaces.tool_codec import tool_config_revision, _provider_token
from .models import PersonalToolConfigOperation, PersonalRouterCredential
from .tool_profiles import get_profile

WRITE = "tool_setup.write_codex_config_fenced"
BARRIER = "tool_setup.fence_codex_config"


def _receipt(command):
    if command is None or command.status != "succeeded":
        raise WorkspaceConfigConflict("Computer recovery receipt is not confirmed. Retry recovery after reconnecting.")
    try:
        result = json.loads(decrypt_secret(command.encrypted_result))
        if not isinstance(result, dict):
            raise ValueError()
        content = result["content"]
        if (not isinstance(content, str) or len(content.encode()) > 256 * 1024 or
                result.get("revision") != tool_config_revision(content) or
                result.get("fence") != {"namespace":str(command.device_id), "sequence":command.server_sequence}):
            raise ValueError()
    except (KeyError, ValueError, TypeError, InvalidToken):
        raise WorkspaceConfigConflict("Computer recovery receipt cannot be verified.") from None
    return content


def verify_barrier(op, command, content):
    if (command is None or op.command is None or op.command.operation != WRITE or
            command.pk != op.fence_command_id or command.operation != BARRIER or
            command.device_id != op.command.device_id or command.server_sequence <= op.command.server_sequence or
            _receipt(command) != content):
        raise WorkspaceConfigConflict("The recovery barrier is unavailable or was superseded.")


@transaction.atomic
def begin(*, request, session, operation_id):
    from .tool_apply import _lock_device
    profile = get_profile(request=request, session_id=session.pk)
    if profile is None:
        raise WorkspaceNotFound()
    device = _lock_device(session, profile)
    op = PersonalToolConfigOperation.objects.select_for_update().filter(pk=operation_id, profile=profile).first()
    if op is None:
        raise WorkspaceNotFound()
    if not op.active:
        raise WorkspaceConfigConflict("This operation is no longer pending. Reload Tool Setup before choosing another action.")
    if op.command is None or op.command.operation != WRITE or op.command.device_id != device.pk:
        raise WorkspaceConfigConflict("This legacy or missing write receipt cannot be safely fenced. Do not replay it; repair its original Runtime before continuing.")
    op.fence_command = enqueue_tool_config_fenced(connection=session.connection, payload={}, barrier=True,
        idempotency_key="personal-tool-fence-" + uuid.uuid4().hex)
    op.state = "recovering"
    op.save(update_fields=["state", "fence_command"])
    return op


def _old_configuration(*, request, op):
    try:
        text = decrypt_secret(op.encrypted_before_config)
        parsed = tomllib.loads(text)
        if len(text.encode()) > 256 * 1024 or tool_config_revision(text) != op.expected_revision:
            raise ValueError()
    except (ValueError, TypeError, InvalidToken):
        raise WorkspaceConfigConflict("The previous configuration cannot be verified; keep the local file and review API setup instead.") from None
    token = _provider_token(parsed)
    # Never restore a revoked/expired Nexus token from an encrypted backup.
    if token.startswith("np-router-"):
        from .router_credentials import _current
        row = PersonalRouterCredential.objects.filter(token_hash=hashlib.sha256(token.encode()).hexdigest()).first()
        try:
            if row is None or (op.old_credential_id and row.pk != op.old_credential_id):
                raise ValueError()
            _current(request, row.pk)
        except (ValueError, exceptions.APIException):
            raise WorkspaceConfigConflict("The previous Nexus credential is no longer valid; keep the local file and create a fresh configuration.") from None
    elif op.old_credential_id:
        raise WorkspaceConfigConflict("The previous credential does not match the backup; it will not be restored.")
    from .agent_tool_setup import validate_backup
    validate_backup(request,op,parsed)
    return text


@transaction.atomic
def prepare_restore(*, request, session, operation_id, barrier_id, content):
    from .tool_apply import _lock_device
    profile = get_profile(request=request, session_id=session.pk)
    _lock_device(session, profile)
    op = PersonalToolConfigOperation.objects.select_for_update().get(pk=operation_id, profile=profile)
    if not op.active or op.fence_command_id != barrier_id:
        raise WorkspaceConfigConflict("This recovery was superseded; reload Tool Setup.")
    verify_barrier(op, op.fence_command, content)
    if tool_config_revision(content) != op.target_revision:
        raise WorkspaceConfigConflict("The local file changed; recovery will not overwrite those changes.")
    before = _old_configuration(request=request, op=op)
    op.restore_command = enqueue_tool_config_fenced(connection=session.connection,
        payload={"content":before, "expected_revision":op.target_revision},
        idempotency_key="personal-tool-restore-" + uuid.uuid4().hex)
    op.save(update_fields=["restore_command"])
    return op


@transaction.atomic
def close_pending(*, request, session, operation_id, proof_id, content, state, restored=False):
    from .tool_apply import _lock_device, _audit
    profile = get_profile(request=request, session_id=session.pk)
    _lock_device(session, profile)
    op = PersonalToolConfigOperation.objects.select_for_update().get(pk=operation_id, profile=profile)
    if not op.active or op.state != "recovering":
        raise WorkspaceConfigConflict("This operation is no longer awaiting recovery.")
    if profile.config_revision != op.profile_revision or profile.router_credential_id != op.old_credential_id:
        raise WorkspaceConfigConflict("The managed profile changed; recovery will not overwrite it.")
    if op.section == 'agents':
        from .agent_tool_setup import check_profile
        check_profile(op,profile)
    if restored:
        command = op.restore_command
        if (command is None or op.command is None or op.fence_command is None or
                command.pk != proof_id or command.operation != WRITE or
                command.device_id != op.command.device_id or command.server_sequence <= op.fence_command.server_sequence or
                _receipt(command) != content or tool_config_revision(content) != op.expected_revision):
            raise WorkspaceConfigConflict("The restored configuration could not be verified. Retry recovery.")
        # The prior key may have been revoked while the remote command ran.
        # Do not settle that as a recovered working configuration.
        _old_configuration(request=request, op=op)
    else:
        if op.fence_command_id != proof_id:
            raise WorkspaceConfigConflict("The recovery snapshot was superseded.")
        verify_barrier(op, op.fence_command, content)
    if op.section == 'agents':
        from .agent_tool_setup import revoke_prepared
        revoke_prepared(op)
    elif op.credential_created:
        PersonalRouterCredential.objects.filter(pk=op.credential_id, revoked_at__isnull=True).update(revoked_at=timezone.now())
    # Keep the prior profile/key, do not revive the key from a completed rotation.
    profile.config_revision = tool_config_revision(content)
    profile.save(update_fields=["config_revision", "updated_at"])
    op.state, op.active, op.completed_at, op.error_code = state, False, timezone.now(), ""
    op.save(update_fields=["state", "active", "completed_at", "error_code"])
    _audit(request, op, "workspaces.tool_setup.recovered")
    return op


def recover(*, request, session_id, data):
    from . import tool_setup, tool_apply
    session = tool_setup._session(request, session_id)
    op = begin(request=request, session=session, operation_id=data["operation_id"])
    try:
        wait_for_runtime_command(op.fence_command, timeout_seconds=32)
    except exceptions.APIException:
        raise WorkspaceConfigConflict("Computer recovery could not be confirmed. Reconnect and retry; no configuration write was replayed.") from None
    op.fence_command.refresh_from_db()
    content = _receipt(op.fence_command)
    verify_barrier(op, op.fence_command, content)
    current = tool_config_revision(content)
    if current != data["expected_revision"]:
        raise WorkspaceConfigConflict("The Computer configuration changed. Reload it before recovering.")
    action = data["action"]
    if action == "keep_local":
        op = close_pending(request=request, session=session, operation_id=op.pk, proof_id=op.fence_command_id,
            content=content, state="kept_local")
    elif current == op.expected_revision:
        op = close_pending(request=request, session=session, operation_id=op.pk, proof_id=op.fence_command_id,
            content=content, state="rolled_back")
    elif current != op.target_revision:
        raise WorkspaceConfigConflict("The file contains local changes. Keep the local file explicitly; it will not be overwritten.")
    elif action == "restore_previous":
        op = prepare_restore(request=request, session=session, operation_id=op.pk, barrier_id=op.fence_command_id, content=content)
        try:
            wait_for_runtime_command(op.restore_command, timeout_seconds=32)
        except exceptions.APIException:
            raise WorkspaceConfigConflict("Restoration is unconfirmed. Retry recovery; do not repeat the original apply.") from None
        op.restore_command.refresh_from_db()
        restored_content = _receipt(op.restore_command)
        # A new read detects local edits after the write receipt.
        _, fresh, _ = tool_setup._read(session)
        if fresh != restored_content:
            raise WorkspaceConfigConflict("The file changed after restoration; reload before recovering.")
        op = close_pending(request=request, session=session, operation_id=op.pk, proof_id=op.restore_command_id,
            content=restored_content, state="rolled_back", restored=True)
    else:
        _, fresh, parsed = tool_setup._read(session)
        if fresh != content:
            raise WorkspaceConfigConflict("The file changed after the recovery snapshot; reload before recovering.")
        op = tool_apply.finish(request=request, session_id=session_id, op_id=op.pk, content=content, parsed=parsed, recovery=True)
    result = tool_setup.get_workspace_tool_config(request=request, session_id=session_id)
    result["operation"] = {"id":str(op.pk), "state":op.state}
    return result
