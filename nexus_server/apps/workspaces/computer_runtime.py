from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone as datetime_timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from django.conf import settings
from django.core import signing
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from rest_framework import exceptions, status

from apps.audit.services import log_audit
from apps.common.resource_limits import enforce_tenant_resource_quota
from apps.common.crypto import decrypt_secret, encrypt_secret
from apps.common.models import SoftDeleteModel
from apps.common.subjects import hash_token, request_subject
from apps.common.request_context import get_tenant_from_request
from apps.tenancy.models import Tenant
from .context_policy import pairing_project, connection_context_valid, scope_connections

from .models import (
    ComputerRuntimeCommand,
    ComputerRuntimeDevice,
    ComputerRuntimeEnrollment,
    WorkspaceConnection,
)
from .connection_core import (
    RunnerResult,
    WorkspaceError,
    WorkspaceNotFound,
    get_workspace_connection,
    require_workspace_own,
    resolve_project,
)


PROTOCOL_VERSION = 1
MAX_PAIRING_CA_BYTES = 64 * 1024
CAPABILITY_VERSIONS = {
    "workspace.v1": 1,
    "terminal.v1": 1,
    "terminal.stream.v1": 1,
    "browser.v1": 1,
    "tool_setup.v1": 1,
    "tool_setup.cas.v1": 1,
    "tool_setup.cas.v2": 1,
}
TERMINAL_COMMAND_STATUSES = {
    ComputerRuntimeCommand.STATUS_SUCCEEDED,
    ComputerRuntimeCommand.STATUS_FAILED,
    ComputerRuntimeCommand.STATUS_CANCELED,
    ComputerRuntimeCommand.STATUS_EXPIRED,
}


class ComputerRuntimeError(WorkspaceError):
    default_code = "COMPUTER_RUNTIME_ERROR"


class ComputerRuntimeOffline(ComputerRuntimeError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "COMPUTER_RUNTIME_OFFLINE"
    default_detail = "Nexus Computer Runtime is offline."


class ComputerCapabilityUnavailable(ComputerRuntimeError):
    status_code = status.HTTP_409_CONFLICT
    default_code = "COMPUTER_CAPABILITY_UNAVAILABLE"
    default_detail = "The attached Computer does not provide the required Runtime capability."


class LegacySSHDisabled(ComputerRuntimeError):
    status_code = status.HTTP_410_GONE
    default_code = "LEGACY_SSH_DISABLED"
    default_detail = "Cloud-initiated SSH Computers are disabled. Pair Nexus Computer Runtime instead."


def _safe_runtime_mapping(value: Any, *, max_items: int = 32) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key, item in list(value.items())[:max_items]:
        name = str(key).strip()
        if not name or len(name) > 96:
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            result[name] = item
    return result


def _normalize_capabilities(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for name, version in value.items():
        key = str(name).strip()
        if key not in CAPABILITY_VERSIONS:
            continue
        try:
            parsed = int(version)
        except (TypeError, ValueError):
            continue
        if 1 <= parsed <= CAPABILITY_VERSIONS[key]:
            result[key] = parsed
    return result


def _public_key(value: str) -> tuple[Ed25519PublicKey, str, str]:
    try:
        loaded = serialization.load_pem_public_key(str(value).encode("ascii"))
    except (ValueError, TypeError, UnicodeEncodeError) as exc:
        raise ComputerRuntimeError("Computer public key must be a PEM Ed25519 public key.") from exc
    if not isinstance(loaded, Ed25519PublicKey):
        raise ComputerRuntimeError("Computer public key must use Ed25519.")
    canonical = loaded.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    raw = loaded.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return loaded, canonical, hashlib.sha256(raw).hexdigest()


def _cloud_origin(request) -> str:
    configured = str(getattr(settings, "NEXUS_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    if configured:
        parsed = urlsplit(configured)
    else:
        parsed = urlsplit(request.build_absolute_uri("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ComputerRuntimeError("Nexus Cloud public origin is not configured.")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def _pairing_cloud_trust() -> dict[str, str]:
    configured = str(getattr(settings, "NEXUS_COMPUTER_RUNTIME_CA_FILE", "") or "").strip()
    if not configured:
        return {}
    path = Path(configured).expanduser().resolve()
    if not path.is_file():
        raise ComputerRuntimeError("Computer Runtime Cloud CA file is unavailable.")
    try:
        value = path.read_bytes()
    except OSError as exc:
        raise ComputerRuntimeError("Computer Runtime Cloud CA file could not be read.") from exc
    if not value or len(value) > MAX_PAIRING_CA_BYTES:
        raise ComputerRuntimeError("Computer Runtime Cloud CA bundle is empty or too large.")
    if b"PRIVATE KEY" in value:
        raise ComputerRuntimeError("Computer Runtime Cloud CA bundle must not contain a private key.")
    try:
        certificates = x509.load_pem_x509_certificates(value)
    except ValueError as exc:
        raise ComputerRuntimeError("Computer Runtime Cloud CA bundle is not valid PEM.") from exc
    if not certificates:
        raise ComputerRuntimeError("Computer Runtime Cloud CA bundle contains no certificates.")
    canonical = b"".join(
        certificate.public_bytes(serialization.Encoding.PEM) for certificate in certificates
    )
    encoded = base64.urlsafe_b64encode(canonical).decode("ascii").rstrip("=")
    return {
        "trust": "pinned-pem",
        "ca": encoded,
        "ca_sha256": hashlib.sha256(canonical).hexdigest(),
    }


@transaction.atomic
def create_pairing_code(*, request, data: dict[str, Any]) -> dict[str, Any]:
    tenant = get_tenant_from_request(request)
    require_workspace_own(request=request, tenant=tenant, action="workspace.connection.create_own")
    subject = request_subject(request)
    if subject.principal_type == "anonymous":
        raise exceptions.NotAuthenticated("Authentication is required.")
    Tenant.objects.select_for_update(no_key=True).get(pk=tenant.pk)
    enforce_tenant_resource_quota(tenant=tenant, resource="remote_workspace")
    project = pairing_project(request=request, tenant=tenant, project_id=data.get("project_id"))
    token = "nxc_pair_" + secrets.token_urlsafe(32)
    ttl = max(60, min(int(getattr(settings, "NEXUS_COMPUTER_PAIRING_TTL_SECONDS", 600)), 3600))
    requested_name = str(data.get("name") or "My Computer").strip()
    name = requested_name
    maximum_name_length = WorkspaceConnection._meta.get_field("name").max_length
    suffix = 2
    while WorkspaceConnection.objects.filter(
        tenant=tenant,
        owner_subject_hash=subject.subject_hash,
        name=name,
        status=SoftDeleteModel.STATUS_ACTIVE,
    ).exists():
        # Reserve suffix space before truncating the stem. Truncating the whole
        # result repeats a full-length name forever while holding the tenant lock.
        discriminator = f" ({suffix})"
        name = requested_name[:maximum_name_length - len(discriminator)] + discriminator
        suffix += 1
    connection = WorkspaceConnection.objects.create(
        tenant=tenant,
        project=project,
        name=name,
        connection_type=WorkspaceConnection.TYPE_RUNTIME,
        ssh_host="",
        ssh_port=22,
        ssh_user="",
        owner_subject_type=subject.principal_type,
        owner_subject_hash=subject.subject_hash,
        workspace_root=str(data.get("workspace_root") or "~/.nexus"),
        encrypted_private_key="",
        encrypted_password="",
        last_test_status=WorkspaceConnection.TEST_UNKNOWN,
        created_by=request.user,
        metadata={"runtime_pairing": "pending"},
    )
    enrollment = ComputerRuntimeEnrollment.objects.create(
        tenant=tenant,
        project=project,
        connection=connection,
        owner_subject_type=subject.principal_type,
        owner_subject_hash=subject.subject_hash,
        token_hash=hash_token(token),
        expires_at=timezone.now() + timedelta(seconds=ttl),
        created_by=request.user,
    )
    origin = _cloud_origin(request)
    pairing_parameters = {"cloud": origin, "code": token, **_pairing_cloud_trust()}
    pairing_url = "nexus-computer://pair?" + urlencode(pairing_parameters)
    log_audit(
        request=request,
        action="workspaces.computer_runtime.pairing.create",
        actor=request.user,
        resource_type="workspace_connection",
        resource_id=connection.id,
        metadata={"expires_at": enrollment.expires_at.isoformat()},
    )
    return {
        "connection_id": str(connection.id),
        "pairing_url": pairing_url,
        "setup_command": f'nexus-computer setup "{pairing_url}"',
        "expires_at": enrollment.expires_at,
    }


@transaction.atomic
def enroll_runtime(*, data: dict[str, Any]) -> dict[str, Any]:
    now = timezone.now()
    enrollment = (
        ComputerRuntimeEnrollment.objects.select_for_update()
        .select_related("connection")
        .filter(token_hash=hash_token(data["pairing_code"]), used_at__isnull=True, expires_at__gt=now)
        .first()
    )
    if enrollment is None or not connection_context_valid(enrollment.connection):
        raise ComputerRuntimeError("Pairing code is invalid, expired, or already used.")
    _key, canonical, fingerprint = _public_key(data["public_key_pem"])
    capabilities = _normalize_capabilities(data.get("capabilities"))
    if not capabilities:
        raise ComputerRuntimeError("Computer Runtime did not advertise any supported capabilities.")
    try:
        device = ComputerRuntimeDevice.objects.create(
            connection=enrollment.connection,
            public_key_pem=canonical,
            public_key_fingerprint=fingerprint,
            platform=data["platform"],
            protocol_version=int(data.get("protocol_version") or PROTOCOL_VERSION),
            capabilities=capabilities,
            facts=_safe_runtime_mapping(data.get("facts")),
        )
    except IntegrityError as exc:
        raise ComputerRuntimeError("This Computer identity is already paired.") from exc
    connection = enrollment.connection
    supplied_name = str(data.get("name") or "").strip()
    if supplied_name:
        connection.name = supplied_name
    connection.metadata = {**(connection.metadata or {}), "runtime_pairing": "paired"}
    connection.last_test_status = WorkspaceConnection.TEST_UNKNOWN
    connection.last_test_error = "Waiting for Nexus Computer Runtime to connect."
    connection.save(update_fields=["name", "metadata", "last_test_status", "last_test_error", "updated_at"])
    enrollment.used_at = now
    enrollment.save(update_fields=["used_at"])
    return {
        "device_id": str(device.id),
        "connection_id": str(connection.id),
        "workspace_root": connection.workspace_root,
        "protocol_version": PROTOCOL_VERSION,
        "heartbeat_seconds": int(getattr(settings, "NEXUS_COMPUTER_RUNTIME_HEARTBEAT_SECONDS", 15)),
    }


@transaction.atomic
def create_runtime_session(*, data: dict[str, Any], request=None) -> dict[str, Any]:
    device = (
        ComputerRuntimeDevice.objects.select_for_update()
        .select_related("connection")
        .filter(id=data["device_id"], revoked_at__isnull=True)
        .first()
    )
    if device is None or device.connection.status == SoftDeleteModel.STATUS_DELETED or not connection_context_valid(device.connection):
        raise WorkspaceNotFound("Computer Runtime was not found.")
    challenge = str(data.get("challenge") or "")
    signature = str(data.get("signature") or "")
    now = timezone.now()
    if not challenge and not signature:
        plaintext = "nxc_ch_" + secrets.token_urlsafe(32)
        device.auth_challenge_hash = hash_token(plaintext)
        device.auth_challenge_expires_at = now + timedelta(seconds=60)
        device.save(update_fields=["auth_challenge_hash", "auth_challenge_expires_at", "updated_at"])
        return {"challenge": plaintext, "expires_at": device.auth_challenge_expires_at}
    if not challenge or not signature:
        raise ComputerRuntimeError("Both challenge and signature are required.")
    if (
        not device.auth_challenge_hash
        or device.auth_challenge_expires_at is None
        or device.auth_challenge_expires_at <= now
        or not secrets.compare_digest(device.auth_challenge_hash, hash_token(challenge))
    ):
        raise ComputerRuntimeError("Computer Runtime session challenge is invalid or expired.")
    try:
        signature_bytes = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        key, _canonical, _fingerprint = _public_key(device.public_key_pem)
        key.verify(signature_bytes, f"nexus-computer-session-v1\n{device.id}\n{challenge}".encode("utf-8"))
    except Exception as exc:
        raise ComputerRuntimeError("Computer Runtime session signature is invalid.") from exc
    ticket = "nxc_ws_" + secrets.token_urlsafe(40)
    device.auth_challenge_hash = ""
    device.auth_challenge_expires_at = None
    device.connect_ticket_hash = hash_token(ticket)
    device.connect_ticket_expires_at = now + timedelta(seconds=60)
    device.save(update_fields=[
        "auth_challenge_hash", "auth_challenge_expires_at", "connect_ticket_hash",
        "connect_ticket_expires_at", "updated_at",
    ])
    origin = _cloud_origin(request) if request is not None else str(getattr(settings, "NEXUS_PUBLIC_BASE_URL", "")).rstrip("/")
    parsed = urlsplit(origin)
    websocket_url = urlunsplit(("wss" if parsed.scheme == "https" else "ws", parsed.netloc, "/ws/computer-runtime/v1/connect/", "", ""))
    return {"ticket": ticket, "expires_at": device.connect_ticket_expires_at, "websocket_url": websocket_url}


@transaction.atomic
def consume_connect_ticket(*, token: str) -> ComputerRuntimeDevice:
    now = timezone.now()
    device = (
        ComputerRuntimeDevice.objects.select_for_update()
        .select_related("connection")
        .filter(connect_ticket_hash=hash_token(token), connect_ticket_expires_at__gt=now, revoked_at__isnull=True)
        .first()
    )
    if device is None:
        raise ComputerRuntimeError("Computer Runtime connect ticket is invalid or expired.")
    if not connection_context_valid(device.connection):
        raise ComputerRuntimeError("Computer Runtime context is unavailable.")
    device.connect_ticket_hash = ""
    device.connect_ticket_expires_at = None
    device.generation += 1
    device.last_client_sequence = 0
    device.last_seen_at = now
    device.save(update_fields=[
        "connect_ticket_hash", "connect_ticket_expires_at", "generation",
        "last_client_sequence", "last_seen_at", "updated_at",
    ])
    return device


@transaction.atomic
def unpair_runtime(*, ticket: str) -> dict[str, Any]:
    device = consume_connect_ticket(token=ticket)
    now = timezone.now()
    device = ComputerRuntimeDevice.objects.select_for_update().get(id=device.id)
    device.revoked_at = now
    device.generation += 1
    device.last_seen_at = now
    device.save(update_fields=["revoked_at", "generation", "last_seen_at", "updated_at"])
    commands = list(device.commands.select_for_update().filter(status__in=[
        ComputerRuntimeCommand.STATUS_QUEUED,
        ComputerRuntimeCommand.STATUS_DISPATCHED,
        ComputerRuntimeCommand.STATUS_RUNNING,
    ]))
    for command in commands:
        command.status = ComputerRuntimeCommand.STATUS_CANCELED
        command.error_code = "COMPUTER_RUNTIME_REVOKED"
        command.error_message = "Computer Runtime was unpaired by the device owner."
        command.completed_at = now
        command.save(update_fields=["status", "error_code", "error_message", "completed_at", "updated_at"])
        _discard_runtime_uploads(command)
    transaction.on_commit(lambda: publish_device_wakeup(str(device.id)))
    return {"device_id": str(device.id), "connection_id": str(device.connection_id), "revoked": True}


def serialize_runtime_device(device: ComputerRuntimeDevice) -> dict[str, Any]:
    facts = device.facts if isinstance(device.facts, dict) else {}
    return {
        "id": str(device.id),
        "connection_id": str(device.connection_id),
        "name": device.connection.name,
        "workspace_root": device.connection.workspace_root,
        "online": device.online,
        "platform": device.platform,
        "protocol_version": device.protocol_version,
        "capabilities": device.capabilities,
        "browser_available": facts.get("browser_available"),
        "browser_name": str(facts.get("browser_name") or ""),
        "last_seen_at": device.last_seen_at,
        "revoked": device.revoked_at is not None,
    }


def list_computers(*, request) -> list[dict[str, Any]]:
    tenant = get_tenant_from_request(request)
    require_workspace_own(request=request, tenant=tenant, action="workspace.connection.read_own")
    subject = request_subject(request)
    connections = (
        WorkspaceConnection.objects.filter(tenant=tenant, owner_subject_hash=subject.subject_hash)
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .select_related("runtime_device")
        .order_by("-created_at")
    )
    connections = scope_connections(request=request, queryset=connections)
    result = []
    for connection in connections:
        if connection.connection_type == WorkspaceConnection.TYPE_SSH:
            result.append({
                "id": str(connection.id), "connection_id": str(connection.id), "name": connection.name,
                "connection_type": "ssh", "online": False, "platform": "", "capabilities": {},
                "legacy_disabled": True, "code": "LEGACY_SSH_DISABLED",
                "message": "Cloud-initiated SSH is disabled. Pair Nexus Computer Runtime instead.",
            })
            continue
        try:
            device = connection.runtime_device
        except ComputerRuntimeDevice.DoesNotExist:
            result.append({
                "id": str(connection.id), "connection_id": str(connection.id), "name": connection.name,
                "connection_type": "runtime", "online": False, "platform": "", "capabilities": {},
                "pairing": True, "code": "COMPUTER_RUNTIME_PAIRING",
                "message": "Waiting for Nexus Computer Runtime to finish pairing.",
            })
        else:
            result.append({**serialize_runtime_device(device), "connection_type": "runtime", "legacy_disabled": False})
    return result


@transaction.atomic
def revoke_computer(*, request, connection_id: str) -> dict[str, Any]:
    connection = get_workspace_connection(request=request, connection_id=connection_id)
    require_workspace_own(request=request, tenant=connection.tenant, action="workspace.connection.update_own", connection=connection)
    if connection.connection_type == WorkspaceConnection.TYPE_SSH:
        raise LegacySSHDisabled()
    try:
        device = ComputerRuntimeDevice.objects.select_for_update().get(connection=connection)
    except ComputerRuntimeDevice.DoesNotExist as exc:
        raise WorkspaceNotFound("Computer Runtime has not completed pairing.") from exc
    now = timezone.now()
    device.revoked_at = now
    device.connect_ticket_hash = ""
    device.connect_ticket_expires_at = None
    device.generation += 1
    device.save(update_fields=["revoked_at", "connect_ticket_hash", "connect_ticket_expires_at", "generation", "updated_at"])
    commands = list(device.commands.select_for_update().filter(status__in=[
        ComputerRuntimeCommand.STATUS_QUEUED,
        ComputerRuntimeCommand.STATUS_DISPATCHED,
        ComputerRuntimeCommand.STATUS_RUNNING,
    ]))
    for command in commands:
        command.status = ComputerRuntimeCommand.STATUS_CANCELED
        command.error_code = "COMPUTER_RUNTIME_REVOKED"
        command.error_message = "Computer Runtime was revoked."
        command.completed_at = now
        command.save(update_fields=["status", "error_code", "error_message", "completed_at", "updated_at"])
        _discard_runtime_uploads(command)
    log_audit(request=request, action="workspaces.computer_runtime.revoke", actor=request.user, resource_type="workspace_connection", resource_id=connection.id)
    publish_device_wakeup(str(device.id))
    return serialize_runtime_device(device)


def required_runtime_capability(operation: str) -> str:
    if operation.startswith("browser."):
        return "browser.v1"
    if operation.startswith("terminal.") or operation == "command.execute":
        return "terminal.v1"
    if operation.startswith("tool_setup."):
        return "tool_setup.v1"
    return "workspace.v1"


def ensure_runtime_connection(connection: WorkspaceConnection, *, operation: str = "workspace.test") -> ComputerRuntimeDevice:
    if not connection_context_valid(connection):
        raise ComputerRuntimeOffline("Computer Runtime context is unavailable.")
    if connection.connection_type == WorkspaceConnection.TYPE_SSH:
        raise LegacySSHDisabled()
    try:
        device = connection.runtime_device
    except ComputerRuntimeDevice.DoesNotExist as exc:
        raise ComputerRuntimeOffline("Nexus Computer Runtime has not completed pairing.") from exc
    if device.revoked_at is not None:
        raise ComputerRuntimeOffline("Nexus Computer Runtime was revoked.")
    if not device.online:
        raise ComputerRuntimeOffline()
    capability = required_runtime_capability(operation)
    if int((device.capabilities or {}).get(capability) or 0) < 1:
        raise ComputerCapabilityUnavailable(f"Nexus Computer Runtime does not provide {capability}.")
    return device


def _payload_summary(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {"operation": operation}
    for key in ("session_id", "browser_session_id", "action"):
        value = payload.get(key)
        if isinstance(value, (str, int, bool)) and len(str(value)) <= 256:
            summary[key] = value
    for key in ("path", "cwd", "workspace_root"):
        value = str(payload.get(key) or "").strip().replace("\\", "/")
        if not value:
            continue
        is_absolute = value.startswith(("/", "~/")) or (len(value) > 2 and value[1:3] == ":/")
        summary[f"{key}_kind"] = "absolute" if is_absolute else "relative"
        summary[f"{key}_depth"] = min(len([part for part in value.split("/") if part not in {"", "."}]), 64)
    return summary


@transaction.atomic
def enqueue_runtime_command(
    *,
    connection: WorkspaceConnection,
    operation: str,
    required_scope: str,
    payload: dict[str, Any],
    timeout_seconds: int = 30,
    idempotency_key: str = "",
    display_run_id: str | None = None,
    caller_subject_hash: str = "",
) -> ComputerRuntimeCommand:
    device = ensure_runtime_connection(connection, operation=operation)
    if idempotency_key:
        existing = ComputerRuntimeCommand.objects.filter(device=device, idempotency_key=idempotency_key).first()
        if existing is not None:
            return existing
    locked = ComputerRuntimeDevice.objects.select_for_update().get(id=device.id)
    locked.last_server_sequence += 1
    locked.save(update_fields=["last_server_sequence", "updated_at"])
    command = ComputerRuntimeCommand.objects.create(
        device=locked,
        connection=connection,
        display_run_id=display_run_id or None,
        caller_subject_hash=caller_subject_hash,
        required_scope=required_scope,
        operation=operation,
        idempotency_key=idempotency_key,
        encrypted_payload=encrypt_secret(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
        payload_summary=_payload_summary(operation, payload),
        server_sequence=locked.last_server_sequence,
        expires_at=timezone.now() + timedelta(seconds=max(1, min(int(timeout_seconds), 900))),
    )
    transaction.on_commit(lambda: publish_device_wakeup(str(device.id)))
    return command


def wait_for_runtime_command(command: ComputerRuntimeCommand, *, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + max(float(timeout_seconds), 0.1)
    while time.monotonic() < deadline:
        command.refresh_from_db()
        if command.status in TERMINAL_COMMAND_STATUSES:
            if command.status == ComputerRuntimeCommand.STATUS_SUCCEEDED:
                try:
                    value = json.loads(decrypt_secret(command.encrypted_result)) if command.encrypted_result else {}
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise ComputerRuntimeError("Computer Runtime command result could not be decoded.") from exc
                if not command.idempotency_key:
                    command.encrypted_result = ""
                    command.save(update_fields=["encrypted_result", "updated_at"])
                return dict(value) if isinstance(value, dict) else {"value": value}
            code = command.error_code or "COMPUTER_RUNTIME_COMMAND_FAILED"
            message = command.error_message or "Nexus Computer Runtime command failed."
            error = ComputerRuntimeError(message)
            error.default_code = code
            raise error
        time.sleep(0.05)
    cancel_runtime_command(command=command, code="COMPUTER_RUNTIME_TIMEOUT", message="Computer Runtime command timed out.")
    error = ComputerRuntimeError("Computer Runtime command timed out.")
    error.default_code = "COMPUTER_RUNTIME_TIMEOUT"
    raise error


def execute_runtime_command(**kwargs) -> dict[str, Any]:
    timeout_seconds = int(kwargs.pop("timeout_seconds", 30))
    command = enqueue_runtime_command(timeout_seconds=timeout_seconds, **kwargs)
    return wait_for_runtime_command(command, timeout_seconds=timeout_seconds + 2)


@transaction.atomic
def cancel_runtime_command(*, command: ComputerRuntimeCommand, code: str = "COMPUTER_RUNTIME_CANCELED", message: str = "Command canceled.") -> None:
    locked = ComputerRuntimeCommand.objects.select_for_update().get(id=command.id)
    if locked.status in TERMINAL_COMMAND_STATUSES:
        return
    device = ComputerRuntimeDevice.objects.select_for_update().get(id=locked.device_id)
    device.last_server_sequence += 1
    device.save(update_fields=["last_server_sequence", "updated_at"])
    locked.status = ComputerRuntimeCommand.STATUS_CANCELED
    locked.error_code = code
    locked.error_message = message[:1024]
    locked.result = {
        **(locked.result or {}),
        "cancel_control": {
            "sequence": device.last_server_sequence,
            "acknowledged": False,
            "last_sent_at": "",
        },
    }
    locked.completed_at = timezone.now()
    locked.save(update_fields=["status", "error_code", "error_message", "result", "completed_at", "updated_at"])
    _discard_runtime_uploads(locked)
    transaction.on_commit(lambda: publish_device_wakeup(str(locked.device_id)))


def command_wire_payload(command: ComputerRuntimeCommand) -> dict[str, Any]:
    try:
        payload = json.loads(decrypt_secret(command.encrypted_payload))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ComputerRuntimeError("Computer Runtime command payload could not be decoded.") from exc
    if not isinstance(payload, dict):
        raise ComputerRuntimeError("Computer Runtime command payload is invalid.")
    upload_token = signing.dumps(
        {"command_id": str(command.id), "device_id": str(command.device_id)},
        salt="nexus-computer-runtime-upload-v1",
        compress=True,
    )
    return {
        "version": PROTOCOL_VERSION,
        "type": "command",
        "sequence": command.server_sequence,
        "command_id": str(command.id),
        "deadline": command.expires_at.isoformat(),
        "required_scope": command.required_scope,
        "operation": command.operation,
        "payload": payload,
        "upload": {
            "endpoint": f"/api/v1/computer-runtime/v1/commands/{command.id}/upload/",
            "token": upload_token,
            "max_bytes": max(int(getattr(settings, "NEXUS_COMPUTER_RUNTIME_UPLOAD_MAX_BYTES", 8 * 1024 * 1024)), 65536),
        },
    }


def _discard_runtime_uploads(command: ComputerRuntimeCommand) -> None:
    result = dict(command.result or {})
    uploads = dict(result.get("uploads") or {})
    for upload in uploads.values():
        if isinstance(upload, dict) and upload.get("storage_name"):
            try:
                default_storage.delete(str(upload["storage_name"]))
            except OSError:
                pass
    if uploads:
        result["uploads"] = {}
        ComputerRuntimeCommand.objects.filter(id=command.id).update(result=result)


@transaction.atomic
def store_runtime_upload(*, command_id: str, token: str, content: bytes, content_type: str) -> dict[str, Any]:
    maximum = max(int(getattr(settings, "NEXUS_COMPUTER_RUNTIME_UPLOAD_MAX_BYTES", 8 * 1024 * 1024)), 65536)
    if not content or len(content) > maximum:
        raise ComputerRuntimeError("Computer Runtime upload exceeded the allowed size.")
    try:
        claims = signing.loads(token, salt="nexus-computer-runtime-upload-v1", max_age=900)
    except signing.BadSignature as exc:
        raise ComputerRuntimeError("Computer Runtime upload token is invalid or expired.") from exc
    if not isinstance(claims, dict) or str(claims.get("command_id")) != str(command_id):
        raise ComputerRuntimeError("Computer Runtime upload token is not bound to this command.")
    command = ComputerRuntimeCommand.objects.select_for_update().filter(
        id=command_id,
        device_id=claims.get("device_id"),
        status__in=[ComputerRuntimeCommand.STATUS_DISPATCHED, ComputerRuntimeCommand.STATUS_RUNNING],
        expires_at__gt=timezone.now(),
    ).first()
    if command is None:
        raise ComputerRuntimeError("Computer Runtime command no longer accepts uploads.")
    if not connection_context_valid(command.connection):
        raise ComputerRuntimeError("Computer Runtime context is unavailable.")
    normalized_type = str(content_type or "application/octet-stream").split(";", 1)[0].strip().lower()[:128]
    result = dict(command.result or {})
    uploads = dict(result.get("uploads") or {})
    if len(uploads) >= 4 or sum(int(item.get("size_bytes") or 0) for item in uploads.values() if isinstance(item, dict)) + len(content) > maximum:
        raise ComputerRuntimeError("Computer Runtime command upload quota was exceeded.")
    upload_id = uuid.uuid4().hex
    storage_name = default_storage.save(
        f"computer-runtime/{command.device_id}/{command.id}/{upload_id}.bin",
        ContentFile(content),
    )
    uploads[upload_id] = {
        "storage_name": storage_name,
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "content_type": normalized_type,
    }
    result["uploads"] = uploads
    command.result = result
    try:
        command.save(update_fields=["result", "updated_at"])
    except Exception:
        default_storage.delete(storage_name)
        raise
    return {"upload_id": upload_id, "size_bytes": len(content), "sha256": uploads[upload_id]["sha256"]}


@transaction.atomic
def consume_runtime_upload(
    *,
    reference: dict[str, Any],
    connection: WorkspaceConnection,
    display_run_id: str | None = None,
    maximum_bytes: int,
) -> tuple[bytes, str]:
    command_id = str(reference.get("command_id") or "")
    upload_id = str(reference.get("upload_id") or "")
    command = ComputerRuntimeCommand.objects.select_for_update().filter(
        id=command_id,
        connection=connection,
    ).first()
    if command is None or (display_run_id and str(command.display_run_id or "") != str(display_run_id)):
        raise ComputerRuntimeError("Computer Runtime upload was not found.")
    result = dict(command.result or {})
    uploads = dict(result.get("uploads") or {})
    upload = dict(uploads.get(upload_id) or {})
    storage_name = str(upload.get("storage_name") or "")
    size_bytes = int(upload.get("size_bytes") or 0)
    if not storage_name or size_bytes <= 0 or size_bytes > maximum_bytes:
        raise ComputerRuntimeError("Computer Runtime upload is invalid or too large.")
    try:
        with default_storage.open(storage_name, "rb") as handle:
            content = handle.read(maximum_bytes + 1)
    except OSError as exc:
        raise ComputerRuntimeError("Computer Runtime upload is unavailable.") from exc
    if len(content) != size_bytes or len(content) > maximum_bytes or hashlib.sha256(content).hexdigest() != upload.get("sha256"):
        raise ComputerRuntimeError("Computer Runtime upload integrity check failed.")
    default_storage.delete(storage_name)
    uploads.pop(upload_id, None)
    result["uploads"] = uploads
    command.result = result
    command.save(update_fields=["result", "updated_at"])
    return content, str(upload.get("content_type") or "application/octet-stream")


@transaction.atomic
def claim_pending_commands(*, device_id: str, limit: int = 16) -> list[dict[str, Any]]:
    device = ComputerRuntimeDevice.objects.filter(pk=device_id).select_related("connection").first()
    if device is None or not connection_context_valid(device.connection):
        return []
    now = timezone.now()
    stale_upload_commands = list(
        ComputerRuntimeCommand.objects.select_for_update()
        .filter(
            device_id=device_id,
            status=ComputerRuntimeCommand.STATUS_SUCCEEDED,
            completed_at__lte=now - timedelta(minutes=15),
        )
        .order_by("completed_at")[:32]
    )
    for stale in stale_upload_commands:
        _discard_runtime_uploads(stale)
    expired = list(ComputerRuntimeCommand.objects.select_for_update().filter(
        device_id=device_id,
        status__in=[
            ComputerRuntimeCommand.STATUS_QUEUED,
            ComputerRuntimeCommand.STATUS_DISPATCHED,
            ComputerRuntimeCommand.STATUS_RUNNING,
        ],
        expires_at__lte=now,
    ))
    for command in expired:
        command.status = ComputerRuntimeCommand.STATUS_EXPIRED
        command.error_code = "COMPUTER_RUNTIME_TIMEOUT"
        command.error_message = "Computer Runtime command expired before execution."
        command.completed_at = now
        command.save(update_fields=["status", "error_code", "error_message", "completed_at", "updated_at"])
        _discard_runtime_uploads(command)
    retry_before = now - timedelta(seconds=5)
    commands = list(
        ComputerRuntimeCommand.objects.select_for_update(skip_locked=True)
        .filter(
            device_id=device_id,
            expires_at__gt=now,
        )
        .filter(
            models.Q(status=ComputerRuntimeCommand.STATUS_QUEUED)
            | models.Q(status=ComputerRuntimeCommand.STATUS_DISPATCHED, dispatched_at__lte=retry_before)
            | models.Q(status=ComputerRuntimeCommand.STATUS_RUNNING, updated_at__lte=retry_before)
        )
        .order_by("server_sequence")[: max(1, min(limit, 64))]
    )
    for command in commands:
        command.status = ComputerRuntimeCommand.STATUS_DISPATCHED
        command.dispatched_at = command.dispatched_at or now
        command.save(update_fields=["status", "dispatched_at", "updated_at"])
    return [command_wire_payload(command) for command in commands]


@transaction.atomic
def claim_cancel_frames(*, device_id: str, limit: int = 32) -> list[dict[str, Any]]:
    """Return cancellation controls until the active Runtime acknowledges them."""

    now = timezone.now()
    retry_before = now - timedelta(seconds=2)
    frames: list[dict[str, Any]] = []
    commands = list(
        ComputerRuntimeCommand.objects.select_for_update(skip_locked=True)
        .filter(device_id=device_id, status=ComputerRuntimeCommand.STATUS_CANCELED)
        .order_by("completed_at")[: max(1, min(limit, 64))]
    )
    for command in commands:
        result = dict(command.result or {})
        control = dict(result.get("cancel_control") or {})
        if not control or bool(control.get("acknowledged")):
            continue
        last_sent_text = str(control.get("last_sent_at") or "")
        try:
            last_sent = datetime.fromisoformat(last_sent_text) if last_sent_text else None
        except ValueError:
            last_sent = None
        if last_sent is not None and timezone.is_naive(last_sent):
            last_sent = timezone.make_aware(last_sent, datetime_timezone.utc)
        if last_sent is not None and last_sent > retry_before:
            continue
        control["last_sent_at"] = now.isoformat()
        result["cancel_control"] = control
        command.result = result
        command.save(update_fields=["result", "updated_at"])
        frames.append({
            "version": PROTOCOL_VERSION,
            "type": "cancel",
            "sequence": int(control.get("sequence") or command.server_sequence),
            "command_id": str(command.id),
            "code": command.error_code or "COMPUTER_RUNTIME_CANCELED",
        })
    return frames


def runtime_generation_is_current(*, device_id: str, generation: int) -> bool:
    device = ComputerRuntimeDevice.objects.filter(pk=device_id).select_related("connection").first()
    if device is None or not connection_context_valid(device.connection):
        return False
    return ComputerRuntimeDevice.objects.filter(
        id=device_id,
        generation=generation,
        revoked_at__isnull=True,
    ).exists()


@transaction.atomic
def apply_runtime_frame(*, device_id: str, generation: int, frame: dict[str, Any]) -> dict[str, Any]:
    device = ComputerRuntimeDevice.objects.select_for_update().filter(id=device_id, revoked_at__isnull=True).first()
    if device is None or int(device.generation) != int(generation):
        raise ComputerRuntimeError("Computer Runtime connection generation is stale.")
    if not connection_context_valid(device.connection):
        raise ComputerRuntimeError("Computer Runtime context is unavailable.")
    try:
        sequence = int(frame.get("sequence") or 0)
    except (TypeError, ValueError):
        raise ComputerRuntimeError("Computer Runtime frame sequence is invalid.")
    if sequence <= device.last_client_sequence:
        return {"duplicate": True, "sequence": sequence}
    device.last_client_sequence = sequence
    device.last_seen_at = timezone.now()
    if frame.get("type") == "hello":
        capabilities = _normalize_capabilities(frame.get("capabilities"))
        if capabilities:
            device.capabilities = capabilities
        facts = _safe_runtime_mapping(frame.get("facts"))
        if facts:
            device.facts = facts
        device.save(update_fields=["last_client_sequence", "last_seen_at", "capabilities", "facts", "updated_at"])
        connection = device.connection
        connection.last_test_status = WorkspaceConnection.TEST_SUCCEEDED
        connection.last_test_error = ""
        connection.last_test_at = timezone.now()
        connection.metadata = {**(connection.metadata or {}), "runtime_pairing": "connected", "last_facts": device.facts}
        connection.save(update_fields=["last_test_status", "last_test_error", "last_test_at", "metadata", "updated_at"])
        return {"hello": True, "sequence": sequence}
    device.save(update_fields=["last_client_sequence", "last_seen_at", "updated_at"])
    frame_type = str(frame.get("type") or "")
    if frame_type == "heartbeat":
        return {"heartbeat": True, "sequence": sequence}
    if frame_type in {
        "terminal_stream_opened",
        "terminal_stream_output",
        "terminal_stream_error",
        "terminal_stream_closed",
    }:
        stream_id = str(frame.get("stream_id") or "")
        if not stream_id or len(stream_id) > 128:
            raise ComputerRuntimeError("Computer Runtime terminal stream identifier is invalid.")
        return {
            "stream": True,
            "sequence": sequence,
            "stream_id": stream_id,
            "stream_type": frame_type,
        }
    command_id = str(frame.get("command_id") or "")
    command = ComputerRuntimeCommand.objects.select_for_update().filter(id=command_id, device=device).first()
    if command is None:
        raise ComputerRuntimeError("Computer Runtime command was not found.")
    if frame_type == "cancel_ack" and command.status == ComputerRuntimeCommand.STATUS_CANCELED:
        result = dict(command.result or {})
        control = dict(result.get("cancel_control") or {})
        control["acknowledged"] = True
        result["cancel_control"] = control
        command.result = result
        command.save(update_fields=["result", "updated_at"])
        return {"sequence": sequence, "command_id": str(command.id), "status": command.status, "cancel_ack": True}
    if command.status in TERMINAL_COMMAND_STATUSES:
        return {"sequence": sequence, "command_id": str(command.id), "status": command.status, "late": True}
    if frame_type == "command_ack":
        if command.status not in TERMINAL_COMMAND_STATUSES:
            command.status = ComputerRuntimeCommand.STATUS_RUNNING
            command.save(update_fields=["status", "updated_at"])
    elif frame_type in {"progress", "stdout", "stderr"}:
        events = list((command.result or {}).get("events") or [])[-199:]
        events.append({
            "type": frame_type,
            "data": str(frame.get("data") or "")[:65536],
            "at": timezone.now().isoformat(),
        })
        command.result = {**(command.result or {}), "events": events}
        command.save(update_fields=["result", "updated_at"])
    elif frame_type == "result":
        result = frame.get("result")
        result_value = result if isinstance(result, dict) else {"value": result}
        command.encrypted_result = encrypt_secret(json.dumps(result_value, ensure_ascii=False, separators=(",", ":")))
        command.result = {"uploads": dict((command.result or {}).get("uploads") or {}), "completed": True}
        command.status = ComputerRuntimeCommand.STATUS_SUCCEEDED
        command.completed_at = timezone.now()
        command.save(update_fields=["encrypted_result", "result", "status", "completed_at", "updated_at"])
    elif frame_type == "error":
        command.status = ComputerRuntimeCommand.STATUS_FAILED
        command.error_code = str(frame.get("code") or "COMPUTER_RUNTIME_COMMAND_FAILED")[:64]
        command.error_message = str(frame.get("message") or "Computer Runtime command failed.")[:1024]
        command.completed_at = timezone.now()
        command.save(update_fields=["status", "error_code", "error_message", "completed_at", "updated_at"])
        _discard_runtime_uploads(command)
    else:
        raise ComputerRuntimeError("Computer Runtime frame type is not supported.")
    return {"sequence": sequence, "command_id": str(command.id), "status": command.status}


def publish_device_wakeup(device_id: str) -> None:
    try:
        import redis

        client = redis.Redis.from_url(str(settings.REDIS_URL), socket_connect_timeout=0.2, socket_timeout=0.2)
        client.publish(f"nexus:computer-runtime:{device_id}", "wake")
        client.close()
    except Exception:
        # Durable DB polling remains the lossless fallback when Redis is unavailable.
        return


def runtime_test(connection: WorkspaceConnection) -> RunnerResult:
    try:
        result = execute_runtime_command(
            connection=connection,
            operation="workspace.test",
            required_scope="connection.list",
            payload={},
            timeout_seconds=10,
        )
    except ComputerRuntimeError as exc:
        return RunnerResult(
            ok=False,
            facts={},
            checks=[{"name": "computer_runtime", "ok": False, "detail": str(exc.detail)}],
            error=str(exc.detail),
        )
    facts = result.get("facts") if isinstance(result.get("facts"), dict) else result
    return RunnerResult(
        ok=True,
        facts=dict(facts),
        checks=[{"name": "computer_runtime", "ok": True, "detail": "Outbound Runtime connected"}],
    )
