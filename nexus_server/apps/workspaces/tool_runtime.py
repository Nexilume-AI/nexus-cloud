"""Opt-in Tool Setup CAS command; the original v1 runner is unchanged."""
from .computer_runtime import ComputerCapabilityUnavailable, ensure_runtime_connection, execute_runtime_command


def require_tool_config_fencing(connection):
    device = ensure_runtime_connection(connection, operation="tool_setup.write_codex_config_fenced")
    if int((device.capabilities or {}).get("tool_setup.cas.v2") or 0) < 1:
        raise ComputerCapabilityUnavailable("Upgrade Computer Runtime to support recoverable Tool Setup writes.")
    return device


def enqueue_tool_config_fenced(*, connection, payload, idempotency_key, barrier=False):
    """Assign the durable device sequence before the command becomes visible."""
    import json
    from django.db import transaction
    from apps.common.crypto import encrypt_secret, decrypt_secret
    from .models import ComputerRuntimeDevice, ComputerRuntimeCommand
    from .connection_core import WorkspaceConfigConflict
    from .computer_runtime import enqueue_runtime_command
    device = require_tool_config_fencing(connection)
    operation = "tool_setup.fence_codex_config" if barrier else "tool_setup.write_codex_config_fenced"
    with transaction.atomic():
        ComputerRuntimeDevice.objects.select_for_update().get(pk=device.pk)
        existing = ComputerRuntimeCommand.objects.filter(device=device, idempotency_key=idempotency_key).first() if idempotency_key else None
        if existing is not None:
            expected = {**payload, "namespace":str(existing.device_id), "sequence":existing.server_sequence}
            if (existing.operation != operation or existing.required_scope != "tool.setup" or
                    json.loads(decrypt_secret(existing.encrypted_payload)) != expected):
                raise WorkspaceConfigConflict("This command key already describes a different Tool Setup operation.")
            return existing
        command = enqueue_runtime_command(connection=connection, operation=operation, required_scope="tool.setup",
            payload=payload, idempotency_key=idempotency_key, timeout_seconds=30)
        if command.status != "queued":
            return command
        command.encrypted_payload = encrypt_secret(json.dumps({**payload,
            "namespace":str(command.device_id), "sequence":command.server_sequence}))
        command.save(update_fields=["encrypted_payload"])
        return command


def require_tool_config_cas(connection):
    device = ensure_runtime_connection(connection, operation="tool_setup.write_codex_config_cas")
    if int((device.capabilities or {}).get("tool_setup.cas.v1") or 0) < 1:
        raise ComputerCapabilityUnavailable("Upgrade Computer Runtime to support revision-checked Tool Setup writes.")
    return device


def write_codex_config_cas(*, connection, content, expected_revision, idempotency_key):
    require_tool_config_cas(connection)
    return execute_runtime_command(connection=connection, operation="tool_setup.write_codex_config_cas",
        required_scope="tool.setup", payload={"content": content, "expected_revision": expected_revision},
        idempotency_key=idempotency_key, timeout_seconds=30)
