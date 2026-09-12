from __future__ import annotations

import base64
import binascii
import hashlib
import json
import secrets
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions, status

from apps.audit.services import log_audit
from apps.common.resource_limits import enforce_tenant_resource_quota
from apps.common.models import SoftDeleteModel
from apps.common.project_scope import scope_queryset_to_current_project
from apps.common.subjects import request_subject
from apps.common.authorization import has_nexus_permission
from apps.tenancy.models import Project, Tenant
from apps.common.request_context import get_tenant_from_request

from .models import MobileCommand, MobileDevice
from .policy import resolve_device_project, device_context_valid, scope_mobile_devices


class MobileError(exceptions.APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Mobile request failed."
    default_code = "MOBILE_ERROR"


class MobileNotFound(MobileError):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "Mobile resource not found."
    default_code = "NOT_FOUND"


class MobileDeviceAuthFailed(MobileError):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "Mobile device token is invalid."
    default_code = "MOBILE_DEVICE_AUTH_FAILED"


class MobilePairingTokenExpired(MobileDeviceAuthFailed):
    default_detail = "Mobile pairing token has expired. Generate a new pairing QR."
    default_code = "MOBILE_PAIRING_TOKEN_EXPIRED"


class MobileDeviceNotReady(MobileError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Enable Nexus Mobile Accessibility control before fetching commands."
    default_code = "MOBILE_DEVICE_NOT_READY"


SAFE_ACTION_RISK = {
    MobileCommand.ACTION_OBSERVE: MobileCommand.RISK_LOW,
    MobileCommand.ACTION_CAPTURE_SCREEN: MobileCommand.RISK_MEDIUM,
    MobileCommand.ACTION_WAIT_FOR_STATE: MobileCommand.RISK_LOW,
    MobileCommand.ACTION_PRESS_BACK: MobileCommand.RISK_LOW,
    MobileCommand.ACTION_TAP_TEXT: MobileCommand.RISK_MEDIUM,
    MobileCommand.ACTION_TAP_COORDINATES: MobileCommand.RISK_MEDIUM,
    MobileCommand.ACTION_SWIPE: MobileCommand.RISK_MEDIUM,
    MobileCommand.ACTION_OPEN_APP: MobileCommand.RISK_MEDIUM,
    MobileCommand.ACTION_TYPE_TEXT: MobileCommand.RISK_HIGH,
}

RISK_RANK = {
    MobileCommand.RISK_LOW: 1,
    MobileCommand.RISK_MEDIUM: 2,
    MobileCommand.RISK_HIGH: 3,
}

SYSTEM_MOBILE_METADATA_KEYS = {
    "paired_at",
    "pairing_token_issued_at",
    "pairing_token_expires_at",
}


def list_mobile_devices(*, request):
    tenant = get_tenant_from_request(request)
    subject = request_subject(request)
    purge_expired_mobile_screenshots(tenant=tenant)
    queryset = scope_queryset_to_current_project(MobileDevice.objects.filter(tenant=tenant), request).exclude(status=SoftDeleteModel.STATUS_DELETED)
    return scope_mobile_devices(request=request, queryset=queryset.filter(owner_subject_hash=subject.subject_hash).order_by("-created_at"))


def aggregate_mobile_status(*, request) -> dict[str, int | str]:
    """Return tenant-wide counts without exposing any caller-owned device identity."""
    from apps.agents.models import AgentMobileLease

    tenant = get_tenant_from_request(request)
    if not (
        has_nexus_permission(request.user, tenant, "mobile.admin")
        or has_nexus_permission(request.user, tenant, "admin")
    ):
        raise exceptions.PermissionDenied("Mobile aggregate status requires admin permission.")
    devices = list(
        MobileDevice.objects.filter(tenant=tenant).exclude(status=SoftDeleteModel.STATUS_DELETED)
    )
    online = sum(device.lifecycle_status == MobileDevice.LIFECYCLE_ONLINE for device in devices)
    busy = AgentMobileLease.objects.filter(
        device__tenant=tenant,
        status=SoftDeleteModel.STATUS_ACTIVE,
        expires_at__gt=timezone.now(),
    ).count()
    failed = (
        MobileCommand.objects.filter(
            tenant=tenant,
            display_run__isnull=False,
            status=MobileCommand.STATUS_FAILED,
            created_at__gte=timezone.now() - timedelta(hours=24),
        )
        .values("device_id")
        .distinct()
        .count()
    )
    return {
        "scope": "workspace",
        "total": len(devices),
        "online": online,
        "offline": len(devices) - online,
        "busy": busy,
        "failed": failed,
        "re_pair_required": sum(not device.owner_subject_hash for device in devices),
    }


def get_mobile_device(*, request, device_id: str) -> MobileDevice:
    tenant = get_tenant_from_request(request)
    subject = request_subject(request)
    device = (
        MobileDevice.objects.filter(tenant=tenant, id=device_id)
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .first()
    )
    if device is None:
        raise MobileNotFound("Mobile device not found.")
    if not can_read_device(user=request.user, tenant=tenant, device=device, subject_hash=subject.subject_hash):
        raise MobileNotFound("Mobile device not found.")
    return device


def get_mobile_screenshot(*, request, device_id: str) -> tuple[bytes, str]:
    device = get_mobile_device(request=request, device_id=device_id)
    if not device.screenshot_available:
        clear_mobile_screenshot(device)
        raise MobileNotFound("A current mobile screenshot is not available.")
    return bytes(device.last_screenshot), device.last_screenshot_content_type or "image/webp"


@transaction.atomic
def create_mobile_device(*, request, data: dict[str, Any]) -> tuple[MobileDevice, str]:
    tenant = get_tenant_from_request(request)
    subject = request_subject(request)
    if subject.principal_type == "anonymous":
        raise exceptions.NotAuthenticated("Authentication is required.")
    if data.get("approval_mode") == MobileDevice.APPROVAL_AUTO:
        require_mobile_policy_admin(request=request, tenant=tenant)
    Tenant.objects.select_for_update(no_key=True).get(pk=tenant.pk)
    enforce_tenant_resource_quota(tenant=tenant, resource="mobile_device")
    project = resolve_device_project(request=request, tenant=tenant, project_id=data.get("project_id"))
    token = generate_mobile_token()
    pairing_expires_at = timezone.now() + timedelta(
        seconds=max(int(getattr(settings, "NEXUS_MOBILE_PAIRING_TTL_SECONDS", 600)), 60)
    )
    device = MobileDevice.objects.create(
        tenant=tenant,
        project=project,
        name=data["name"],
        platform=data.get("platform") or MobileDevice.PLATFORM_ANDROID,
        device_identifier=data.get("device_identifier", ""),
        token_prefix=token[:22],
        token_hash=hash_token(token),
        approval_mode=data.get("approval_mode") or MobileDevice.APPROVAL_CONFIRM_HIGH_RISK,
        capabilities=data.get("capabilities", {}),
        metadata={
            **safe_mobile_metadata(data.get("metadata")),
            "pairing_token_issued_at": timezone.now().isoformat(),
            "pairing_token_expires_at": pairing_expires_at.isoformat(),
        },
        owner_subject_hash=subject.subject_hash,
        owner_principal_type=subject.principal_type,
        created_by=request.user if getattr(request.user, "is_authenticated", False) and getattr(request.user, "pk", None) else None,
    )
    log_audit(
        request=request,
        action="mobile.device.create",
        actor=request.user,
        resource_type="mobile_device",
        resource_id=device.id,
        metadata={"name": device.name, "platform": device.platform, "token_prefix": device.token_prefix},
    )
    return device, token


@transaction.atomic
def update_mobile_device(*, request, device_id: str, data: dict[str, Any]) -> MobileDevice:
    device = get_mobile_device(request=request, device_id=device_id)
    require_mobile_admin(request=request, tenant=device.tenant, device=device)
    if data.get("approval_mode") == MobileDevice.APPROVAL_AUTO and device.approval_mode != MobileDevice.APPROVAL_AUTO:
        require_mobile_policy_admin(request=request, tenant=device.tenant)
    changed: list[str] = []
    for field in ["name", "approval_mode", "capabilities", "status"]:
        if field in data:
            setattr(device, field, data[field])
            changed.append(field)
    if "metadata" in data:
        device.metadata = {
            **(device.metadata or {}),
            **safe_mobile_metadata(data.get("metadata")),
        }
        changed.append("metadata")
    if changed:
        device.save(update_fields=changed + ["updated_at"])
    log_audit(
        request=request,
        action="mobile.device.update",
        actor=request.user,
        resource_type="mobile_device",
        resource_id=device.id,
        metadata={"fields": changed},
    )
    return device


@transaction.atomic
def delete_mobile_device(*, request, device_id: str) -> MobileDevice:
    device = get_mobile_device(request=request, device_id=device_id)
    require_mobile_admin(request=request, tenant=device.tenant, device=device)
    device.commands.filter(
        status__in=[MobileCommand.STATUS_PENDING_APPROVAL, MobileCommand.STATUS_QUEUED, MobileCommand.STATUS_RUNNING]
    ).update(status=MobileCommand.STATUS_CANCELED, completed_at=timezone.now(), updated_at=timezone.now())
    device.agent_run_leases.filter(status=SoftDeleteModel.STATUS_ACTIVE).update(
        status=SoftDeleteModel.STATUS_DELETED,
        deleted_at=timezone.now(),
        updated_at=timezone.now(),
    )
    device.agent_bindings.filter(status=SoftDeleteModel.STATUS_ACTIVE).update(
        status=SoftDeleteModel.STATUS_DELETED,
        deleted_at=timezone.now(),
        updated_at=timezone.now(),
    )
    device.delete()
    log_audit(request=request, action="mobile.device.delete", actor=request.user, resource_type="mobile_device", resource_id=device.id)
    return device


@transaction.atomic
def rotate_mobile_device_token(*, request, device_id: str) -> tuple[MobileDevice, str]:
    device = get_mobile_device(request=request, device_id=device_id)
    require_mobile_admin(request=request, tenant=device.tenant, device=device)
    token = generate_mobile_token()
    device.token_prefix = token[:22]
    device.token_hash = hash_token(token)
    pairing_expires_at = timezone.now() + timedelta(
        seconds=max(int(getattr(settings, "NEXUS_MOBILE_PAIRING_TTL_SECONDS", 600)), 60)
    )
    metadata = dict(device.metadata or {})
    metadata.pop("paired_at", None)
    metadata.update(
        {
            "pairing_token_issued_at": timezone.now().isoformat(),
            "pairing_token_expires_at": pairing_expires_at.isoformat(),
        }
    )
    device.metadata = metadata
    device.save(update_fields=["token_prefix", "token_hash", "metadata", "updated_at"])
    log_audit(request=request, action="mobile.device.token.rotate", actor=request.user, resource_type="mobile_device", resource_id=device.id)
    return device, token


def list_mobile_commands(*, request, device_id: str):
    device = get_mobile_device(request=request, device_id=device_id)
    require_mobile_use(request=request, tenant=device.tenant, device=device)
    return device.commands.exclude(status=SoftDeleteModel.STATUS_DELETED).order_by("-created_at")


@transaction.atomic
def create_mobile_command(*, request, device_id: str, data: dict[str, Any]) -> MobileCommand:
    device = get_mobile_device(request=request, device_id=device_id)
    require_mobile_use(request=request, tenant=device.tenant, device=device)
    command = create_mobile_command_for_device(
        device=device,
        action=data["action"],
        arguments=data.get("arguments", {}),
        risk_level=data.get("risk_level"),
        requires_approval=data.get("requires_approval"),
        ttl_seconds=data.get("ttl_seconds") or 120,
        actor=request.user,
    )
    log_audit(
        request=request,
        action="mobile.command.create",
        actor=request.user,
        resource_type="mobile_command",
        resource_id=command.id,
        metadata={"device_id": str(device.id), "action": command.action, "risk_level": command.risk_level},
    )
    return command


def create_mobile_command_for_device(
    *,
    device: MobileDevice,
    action: str,
    arguments: dict[str, Any],
    risk_level: str | None = None,
    requires_approval: bool | None = None,
    ttl_seconds: int = 120,
    actor=None,
    display_run=None,
    mobile_binding=None,
    caller_subject_hash: str = "",
    client_request_id=None,
    hosted_agent: bool = False,
) -> MobileCommand:
    inferred_risk = infer_risk(action=action, arguments=arguments)
    requested_risk = risk_level if risk_level in RISK_RANK else inferred_risk
    normalized_risk = max((inferred_risk, requested_risk), key=lambda value: RISK_RANK[value])
    approval_required = command_requires_approval(device=device, risk_level=normalized_risk) or bool(requires_approval)
    if hosted_agent and normalized_risk == MobileCommand.RISK_HIGH:
        approval_required = True
    return MobileCommand.objects.create(
        tenant=device.tenant,
        project=device.project,
        device=device,
        display_run=display_run,
        mobile_binding=mobile_binding,
        caller_subject_hash=caller_subject_hash,
        client_request_id=client_request_id,
        action=action,
        arguments=arguments,
        risk_level=normalized_risk,
        requires_approval=approval_required,
        status=MobileCommand.STATUS_PENDING_APPROVAL if approval_required else MobileCommand.STATUS_QUEUED,
        expires_at=timezone.now() + timedelta(seconds=ttl_seconds),
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )


@transaction.atomic
def approve_mobile_command(*, request, command_id: str) -> MobileCommand:
    command = get_mobile_command(request=request, command_id=command_id)
    require_mobile_admin(request=request, tenant=command.tenant, device=command.device)
    if command.status != MobileCommand.STATUS_PENDING_APPROVAL:
        raise MobileError("Only pending approval commands can be approved.")
    command.status = MobileCommand.STATUS_QUEUED
    command.approved_by = request.user
    command.approved_at = timezone.now()
    command.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
    log_audit(request=request, action="mobile.command.approve", actor=request.user, resource_type="mobile_command", resource_id=command.id)
    return command


@transaction.atomic
def reject_mobile_command(*, request, command_id: str) -> MobileCommand:
    command = get_mobile_command(request=request, command_id=command_id)
    require_mobile_admin(request=request, tenant=command.tenant, device=command.device)
    if command.status != MobileCommand.STATUS_PENDING_APPROVAL:
        raise MobileError("Only pending approval commands can be rejected.")
    command.status = MobileCommand.STATUS_REJECTED
    command.completed_at = timezone.now()
    command.save(update_fields=["status", "completed_at", "updated_at"])
    log_audit(request=request, action="mobile.command.reject", actor=request.user, resource_type="mobile_command", resource_id=command.id)
    return command


@transaction.atomic
def cancel_mobile_command(*, request, command_id: str) -> MobileCommand:
    command = get_mobile_command(request=request, command_id=command_id)
    require_mobile_use(request=request, tenant=command.tenant, device=command.device)
    if command.status not in {MobileCommand.STATUS_PENDING_APPROVAL, MobileCommand.STATUS_QUEUED}:
        raise MobileError("Only pending or queued commands can be canceled.")
    command.status = MobileCommand.STATUS_CANCELED
    command.completed_at = timezone.now()
    command.save(update_fields=["status", "completed_at", "updated_at"])
    log_audit(request=request, action="mobile.command.cancel", actor=request.user, resource_type="mobile_command", resource_id=command.id)
    return command


@transaction.atomic
def delete_mobile_command(*, request, command_id: str) -> MobileCommand:
    command = get_mobile_command(request=request, command_id=command_id)
    require_mobile_admin(request=request, tenant=command.tenant, device=command.device)
    previous_status = command.status
    command.delete()
    log_audit(
        request=request,
        action="mobile.command.delete",
        actor=request.user,
        resource_type="mobile_command",
        resource_id=command.id,
        metadata={"device_id": str(command.device_id), "action": command.action, "previous_status": previous_status},
    )
    return command


def get_mobile_command(*, request, command_id: str) -> MobileCommand:
    tenant = get_tenant_from_request(request)
    subject = request_subject(request)
    command = (
        MobileCommand.objects.select_related("device", "tenant", "project")
        .filter(tenant=tenant, id=command_id)
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .first()
    )
    if command is None or not can_read_device(
        user=request.user,
        tenant=tenant,
        device=command.device,
        subject_hash=subject.subject_hash,
    ):
        raise MobileNotFound("Mobile command not found.")
    return command


@transaction.atomic
def device_heartbeat(*, request, device_id: str, data: dict[str, Any]) -> MobileDevice:
    device = authenticate_device_request(request=request, device_id=device_id)
    if device.last_screenshot and not device.screenshot_available:
        clear_mobile_screenshot(device)
    device.mark_seen()
    changed = ["online_status", "last_seen_at"]
    metadata = dict(device.metadata or {})
    if not device.paired_at:
        metadata["paired_at"] = timezone.now().isoformat()
    if "metadata" in data:
        metadata.update(safe_mobile_metadata(data.get("metadata")))
    if metadata != (device.metadata or {}):
        device.metadata = metadata
        changed.append("metadata")
    for field in ["current_package", "current_activity", "capabilities"]:
        if field in data:
            setattr(device, field, data[field])
            changed.append(field)
    if "online_status" in data:
        device.online_status = data["online_status"]
    if "observation" in data:
        device.last_observation = data["observation"]
        changed.append("last_observation")
    device.save(update_fields=list(set(changed + ["updated_at"])))
    return device


@transaction.atomic
def next_device_command(*, request, device_id: str) -> MobileCommand | None:
    device = authenticate_device_request(request=request, device_id=device_id)
    if not bool((device.capabilities or {}).get("accessibility")):
        raise MobileDeviceNotReady()
    device.mark_seen()
    device.save(update_fields=["online_status", "last_seen_at", "updated_at"])
    now = timezone.now()
    expired = device.commands.filter(
        status__in=[MobileCommand.STATUS_PENDING_APPROVAL, MobileCommand.STATUS_QUEUED],
        expires_at__lt=now,
    )
    expired.update(status=MobileCommand.STATUS_CANCELED, completed_at=now, error="Command expired.", updated_at=now)
    command = (
        device.commands.select_for_update()
        .filter(status=MobileCommand.STATUS_QUEUED)
        .filter(models_expires_filter(now))
        .order_by("created_at")
        .first()
    )
    if command is None:
        return None
    command.status = MobileCommand.STATUS_RUNNING
    command.dispatched_at = now
    command.save(update_fields=["status", "dispatched_at", "updated_at"])
    return command


@transaction.atomic
def complete_device_command(*, request, command_id: str, data: dict[str, Any]) -> MobileCommand:
    command = authenticate_command_device_request(request=request, command_id=command_id)
    if command.status != MobileCommand.STATUS_RUNNING:
        raise MobileError("Only running commands can be completed.")
    command.status = data["status"]
    command.error = data.get("error", "")
    result = dict(data.get("result") or {})
    if command.action == MobileCommand.ACTION_CAPTURE_SCREEN:
        if command.status == MobileCommand.STATUS_SUCCEEDED:
            try:
                screenshot, content_type = decode_mobile_screenshot(result)
            except ValueError as exc:
                command.status = MobileCommand.STATUS_FAILED
                command.result = {}
                command.error = f"SCREEN_CAPTURE_INVALID: {exc}"
            else:
                command.screenshot = screenshot
                command.screenshot_content_type = content_type
                if not command.display_run_id:
                    device = command.device
                    device.last_screenshot = screenshot
                    device.last_screenshot_content_type = content_type
                    device.last_screenshot_captured_at = timezone.now()
                    device.mark_seen()
                    device.save(
                        update_fields=[
                            "last_screenshot",
                            "last_screenshot_content_type",
                            "last_screenshot_captured_at",
                            "online_status",
                            "last_seen_at",
                            "updated_at",
                        ]
                    )
                command.result = {
                    "captured": True,
                    "content_type": content_type,
                    "byte_size": len(screenshot),
                    "width": safe_positive_int(result.get("width")),
                    "height": safe_positive_int(result.get("height")),
                }
        else:
            result.pop("screenshot_base64", None)
            command.result = result
    else:
        command.result = result
    command.completed_at = timezone.now()
    command.save(
        update_fields=[
            "status",
            "result",
            "error",
            "screenshot",
            "screenshot_content_type",
            "completed_at",
            "updated_at",
        ]
    )
    if command.action == MobileCommand.ACTION_OBSERVE and command.result and not command.display_run_id:
        device = command.device
        device.last_observation = command.result
        device.mark_seen()
        device.save(update_fields=["last_observation", "online_status", "last_seen_at", "updated_at"])
    return command


def decode_mobile_screenshot(result: dict[str, Any]) -> tuple[bytes, str]:
    encoded = str(result.pop("screenshot_base64", "") or "")
    content_type = str(result.get("content_type") or "").lower()
    max_bytes = max(int(getattr(settings, "NEXUS_MOBILE_SCREENSHOT_MAX_BYTES", 1_048_576)), 64_000)
    if not encoded or len(encoded) > ((max_bytes * 4) // 3) + 16:
        raise ValueError("Screenshot data is missing or too large.")
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Screenshot data is not valid base64.") from exc
    if not payload or len(payload) > max_bytes:
        raise ValueError("Screenshot exceeds the allowed size.")
    detected_type = detect_image_content_type(payload)
    if detected_type is None or content_type != detected_type:
        raise ValueError("Screenshot content type does not match its data.")
    return payload, detected_type


def detect_image_content_type(payload: bytes) -> str | None:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(payload) >= 12 and payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        return "image/webp"
    return None


def safe_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if 0 < parsed <= 10_000 else None


def clear_mobile_screenshot(device: MobileDevice) -> None:
    if not (device.last_screenshot or device.last_screenshot_captured_at or device.last_screenshot_content_type):
        return
    device.last_screenshot = None
    device.last_screenshot_content_type = ""
    device.last_screenshot_captured_at = None
    device.save(
        update_fields=[
            "last_screenshot",
            "last_screenshot_content_type",
            "last_screenshot_captured_at",
            "updated_at",
        ]
    )


def purge_expired_mobile_screenshots(*, tenant: Tenant) -> None:
    ttl_seconds = max(int(getattr(settings, "NEXUS_MOBILE_SCREENSHOT_TTL_SECONDS", 300)), 30)
    MobileDevice.objects.filter(
        tenant=tenant,
        last_screenshot_captured_at__lte=timezone.now() - timedelta(seconds=ttl_seconds),
    ).exclude(last_screenshot__isnull=True).update(
        last_screenshot=None,
        last_screenshot_content_type="",
        last_screenshot_captured_at=None,
        updated_at=timezone.now(),
    )
    # Hosted-Agent captures belong to one Run/Command and use the same short
    # retention window; they must never become a device-level shared asset.
    MobileCommand.objects.filter(
        tenant=tenant,
        action=MobileCommand.ACTION_CAPTURE_SCREEN,
        completed_at__lte=timezone.now() - timedelta(seconds=ttl_seconds),
    ).exclude(screenshot__isnull=True).update(
        screenshot=None,
        screenshot_content_type="",
        updated_at=timezone.now(),
    )


def export_mobile_mcp(*, request, device_id: str) -> dict[str, Any]:
    device = get_mobile_device(request=request, device_id=device_id)
    require_mobile_use(request=request, tenant=device.tenant, device=device)
    base = request.build_absolute_uri("/") if request else "/"
    name = f"nexus_mobile_{str(device.id).replace('-', '_')[:12]}"
    return {
        "mcpServers": {
            name: {
                "type": "streamable-http",
                "name": name,
                "device_id": str(device.id),
                "tenant_id": str(device.tenant_id),
                "url": f"{base.rstrip('/')}/api/v1/mobile-devices/{device.id}/mcp/",
                "headers": {
                    "Authorization": "Bearer ${NEXUS_API_KEY}",
                    "X-Nexus-Tenant": str(device.tenant_id),
                    "X-Nexus-Project": str(device.project_id or getattr(request, "project_id", "") or ""),
                },
            }
        }
    }


def handle_mobile_mcp(*, request, device_id: str, body: bytes) -> tuple[int, bytes]:
    device = get_mobile_device(request=request, device_id=device_id)
    require_mobile_use(request=request, tenant=device.tenant, device=device)
    payload = json.loads(body.decode("utf-8") or "{}")
    request_id = payload.get("id")
    method = payload.get("method")
    if method == "initialize":
        return 200, json_response(
            {
                "jsonrpc": "2.0",
                "result": {
                    "protocolVersion": request.META.get("HTTP_MCP_PROTOCOL_VERSION", "2025-06-18"),
                    "serverInfo": {"name": "nexus_mobile", "version": "0.1.0"},
                    "capabilities": {"tools": {}},
                },
                "id": request_id,
            }
        )
    if method == "tools/list":
        return 200, json_response({"jsonrpc": "2.0", "result": {"tools": mobile_mcp_tools()}, "id": request_id})
    if method == "tools/call":
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        tool_name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        result = call_mobile_tool(device=device, tool_name=tool_name, arguments=arguments, actor=request.user)
        structured = result.get("structuredContent") if isinstance(result, dict) else {}
        log_audit(
            request=request,
            action="mobile.mcp.tool.call",
            actor=request.user,
            resource_type="mobile_device",
            resource_id=device.id,
            metadata={
                "tool": tool_name,
                "command_id": str(structured.get("command_id", "")) if isinstance(structured, dict) else "",
            },
        )
        return 200, json_response({"jsonrpc": "2.0", "result": result, "id": request_id})
    return 200, json_response({"jsonrpc": "2.0", "error": {"code": -32601, "message": "Method not found"}, "id": request_id})


def call_mobile_tool(*, device: MobileDevice, tool_name: str, arguments: dict[str, Any], actor=None) -> dict[str, Any]:
    if tool_name == "mobile_observe":
        return {
            "content": [{"type": "text", "text": json.dumps(device.last_observation or {}, ensure_ascii=False)}],
            "isError": False,
        }
    action_map = {
        "mobile_capture_screen": MobileCommand.ACTION_CAPTURE_SCREEN,
        "mobile_tap_text": MobileCommand.ACTION_TAP_TEXT,
        "mobile_tap_coordinates": MobileCommand.ACTION_TAP_COORDINATES,
        "mobile_type_text": MobileCommand.ACTION_TYPE_TEXT,
        "mobile_swipe": MobileCommand.ACTION_SWIPE,
        "mobile_press_back": MobileCommand.ACTION_PRESS_BACK,
        "mobile_open_app": MobileCommand.ACTION_OPEN_APP,
        "mobile_wait_for_state": MobileCommand.ACTION_WAIT_FOR_STATE,
    }
    action = action_map.get(tool_name)
    if not action:
        raise MobileError(f"Unknown mobile tool: {tool_name}")
    command = create_mobile_command_for_device(device=device, action=action, arguments=arguments, actor=actor)
    text = f"mobile command {command.id} queued with status={command.status}"
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": {
            "command_id": str(command.id),
            "device_id": str(device.id),
            "status": command.status,
            "requires_approval": command.requires_approval,
            "risk_level": command.risk_level,
        },
        "isError": False,
    }


def mobile_mcp_tools() -> list[dict[str, Any]]:
    return [
        tool_schema("mobile_observe", "Return the latest Android accessibility observation.", {}),
        tool_schema("mobile_capture_screen", "Request one protected Android screen capture for Nexus Console.", {}),
        tool_schema("mobile_tap_text", "Tap the first visible element matching text.", {"text": {"type": "string"}}),
        tool_schema(
            "mobile_tap_coordinates",
            "Tap normalized screen coordinates.",
            {"x": {"type": "number", "minimum": 0, "maximum": 1}, "y": {"type": "number", "minimum": 0, "maximum": 1}},
        ),
        tool_schema("mobile_type_text", "Type text into the focused Android field.", {"text": {"type": "string"}}),
        tool_schema(
            "mobile_swipe",
            "Swipe between normalized screen coordinates.",
            {
                "start_x": {"type": "number"},
                "start_y": {"type": "number"},
                "end_x": {"type": "number"},
                "end_y": {"type": "number"},
                "duration_ms": {"type": "integer"},
            },
        ),
        tool_schema("mobile_press_back", "Press Android back.", {}),
        tool_schema("mobile_open_app", "Open an Android package name.", {"package": {"type": "string"}}),
        tool_schema("mobile_wait_for_state", "Wait until text/package appears.", {"text": {"type": "string"}, "timeout_ms": {"type": "integer"}}),
    ]


def tool_schema(name: str, description: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {"type": "object", "properties": properties, "additionalProperties": True},
    }


def authenticate_device_request(*, request, device_id: str) -> MobileDevice:
    token = mobile_token_from_request(request)
    device = MobileDevice.objects.filter(id=device_id).exclude(status=SoftDeleteModel.STATUS_DELETED).first()
    if device is None or device.status != MobileDevice.STATUS_ACTIVE or device.token_hash != hash_token(token) or not device_context_valid(device):
        raise MobileDeviceAuthFailed()
    if not device.paired_at and device.pairing_expires_at and device.pairing_expires_at <= timezone.now():
        raise MobilePairingTokenExpired()
    return device


def authenticate_command_device_request(*, request, command_id: str) -> MobileCommand:
    token = mobile_token_from_request(request)
    command = MobileCommand.objects.select_related("device").filter(id=command_id).exclude(status=SoftDeleteModel.STATUS_DELETED).first()
    if (
        command is None
        or command.device.status != MobileDevice.STATUS_ACTIVE
        or command.device.token_hash != hash_token(token)
        or not device_context_valid(command.device)
    ):
        raise MobileDeviceAuthFailed()
    if (
        not command.device.paired_at
        and command.device.pairing_expires_at
        and command.device.pairing_expires_at <= timezone.now()
    ):
        raise MobilePairingTokenExpired()
    return command


def mobile_token_from_request(request) -> str:
    token = request.META.get("HTTP_X_NEXUS_MOBILE_TOKEN", "")
    if not token:
        auth = request.META.get("HTTP_AUTHORIZATION", "")
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1]
    if not token:
        raise MobileDeviceAuthFailed()
    return token


def can_read_device(*, user, tenant: Tenant, device: MobileDevice, subject_hash: str = "") -> bool:
    return bool(subject_hash and device.owner_subject_hash and secrets.compare_digest(device.owner_subject_hash, subject_hash)) and device_context_valid(device)


def require_mobile_admin(*, request, tenant: Tenant, device: MobileDevice | None = None) -> None:
    subject = request_subject(request)
    if device and device.owner_subject_hash and secrets.compare_digest(device.owner_subject_hash, subject.subject_hash):
        return
    raise MobileNotFound("Mobile device not found.")


def require_mobile_policy_admin(*, request, tenant: Tenant) -> None:
    if has_nexus_permission(request.user, tenant, "mobile.admin") or has_nexus_permission(request.user, tenant, "admin"):
        return
    raise exceptions.PermissionDenied("Mobile policy admin permission is required for automatic approval.")


def require_mobile_use(*, request, tenant: Tenant, device: MobileDevice) -> None:
    subject = request_subject(request)
    if device.owner_subject_hash and secrets.compare_digest(device.owner_subject_hash, subject.subject_hash):
        return
    raise MobileNotFound("Mobile device not found.")


def resolve_project(*, tenant: Tenant, project_id) -> Project | None:
    if not project_id:
        return None
    project = Project.objects.filter(tenant=tenant, id=project_id, status=SoftDeleteModel.STATUS_ACTIVE).first()
    if project is None:
        raise exceptions.NotFound("Project not found.")
    return project


def infer_risk(*, action: str, arguments: dict[str, Any]) -> str:
    risk = SAFE_ACTION_RISK.get(action, MobileCommand.RISK_MEDIUM)
    if action == MobileCommand.ACTION_TAP_TEXT and any(
        word in str(arguments.get("text", "")).lower()
        for word in ["pay", "send", "delete", "购买", "支付", "删除"]
    ):
        return MobileCommand.RISK_HIGH
    return risk


def safe_mobile_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if key not in SYSTEM_MOBILE_METADATA_KEYS}


def command_requires_approval(*, device: MobileDevice, risk_level: str) -> bool:
    if device.approval_mode == MobileDevice.APPROVAL_MANUAL:
        return True
    if device.approval_mode == MobileDevice.APPROVAL_AUTO:
        return False
    return risk_level == MobileCommand.RISK_HIGH


def models_expires_filter(now):
    from django.db.models import Q

    return Q(expires_at__isnull=True) | Q(expires_at__gte=now)


def generate_mobile_token() -> str:
    return f"mnx-mobile-{secrets.token_urlsafe(32)}"


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def json_response(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
