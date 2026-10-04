from __future__ import annotations
from .device_contracts import (MOBILE_CAPABILITIES, MOBILE_CAPABILITY_SET, normalize_mobile_capabilities, current_mobile_declaration, sdk_mobile_declaration)

import base64
import secrets
from datetime import timedelta
from typing import Iterable

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import exceptions, status

from apps.common.models import SoftDeleteModel
from apps.common.subjects import hash_token, request_subject
from apps.mobile.models import MobileCommand, MobileDevice
from apps.mobile.policy import device_context_valid
from apps.mobile.services import MobileNotFound, get_mobile_device, purge_expired_mobile_screenshots
from apps.mobile.services import create_mobile_command_for_device
from apps.mobile.serializers import MobileCommandCreateSerializer
from apps.tenancy.models import Tenant
from apps.common.request_context import get_tenant_from_request

from .models import (
    Agent,
    AgentDisplayRun,
    AgentMobileBinding,
    AgentMobileGrant,
    AgentMobileLease,
    AgentExecutionTask,
    AgentRunInteraction,
)


ACTION_CAPABILITY = {
    MobileCommand.ACTION_OBSERVE: "mobile.observe",
    MobileCommand.ACTION_CAPTURE_SCREEN: "mobile.screen.capture",
    MobileCommand.ACTION_TAP_TEXT: "mobile.tap",
    MobileCommand.ACTION_TAP_COORDINATES: "mobile.tap",
    MobileCommand.ACTION_TYPE_TEXT: "mobile.type_text",
    MobileCommand.ACTION_SWIPE: "mobile.swipe",
    MobileCommand.ACTION_PRESS_BACK: "mobile.press_back",
    MobileCommand.ACTION_PRESS_HOME: "mobile.press_back",
    MobileCommand.ACTION_PRESS_RECENTS: "mobile.press_back",
    MobileCommand.ACTION_LONG_PRESS: "mobile.tap",
    MobileCommand.ACTION_OPEN_APP: "mobile.open_app",
    MobileCommand.ACTION_WAIT_FOR_STATE: "mobile.wait_for_state",
}


class AgentMobileError(exceptions.APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Mobile access is unavailable."
    default_code = "MOBILE_UNAVAILABLE"


class AgentMobileRequired(AgentMobileError):
    default_detail = "This Agent requires a caller-owned Mobile binding."
    default_code = "MOBILE_REQUIRED"


class AgentMobilePermissionRequired(AgentMobileError):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "Grant the Agent access to the required Mobile capabilities."
    default_code = "MOBILE_PERMISSION_REQUIRED"


class AgentMobileUnavailable(AgentMobileError):
    default_detail = "The selected Mobile device is not online and ready."
    default_code = "MOBILE_UNAVAILABLE"


class AgentMobileBusy(AgentMobileError):
    default_detail = "The selected Mobile device is being controlled by another Run."
    default_code = "MOBILE_BUSY"


class AgentMobileInteractiveTransportRequired(AgentMobileError):
    default_detail = "Interactive transport is required for this Mobile action."
    default_code = "INTERACTIVE_TRANSPORT_REQUIRED"


class AgentMobileCommandIdempotencyConflict(AgentMobileError):
    default_detail = "The Mobile command request ID was already used for another action."
    default_code = "MOBILE_COMMAND_IDEMPOTENCY_CONFLICT"








def _reconcile_mobile_policy_reduction(*, agent: Agent, removed: set[str]) -> None:
    if not removed:
        return

    now = timezone.now()
    changed_subjects: list[tuple[str, str | None, str]] = []
    grants = AgentMobileGrant.objects.select_for_update().filter(agent=agent).exclude(
        status=SoftDeleteModel.STATUS_DELETED,
    )
    for grant in grants:
        current = normalize_mobile_capabilities(grant.scopes)
        trimmed = [scope for scope in current if scope not in removed]
        if trimmed == current:
            continue
        grant.scopes = trimmed
        grant.save(update_fields=["scopes", "updated_at"])
        changed_subjects.append(
            (str(grant.tenant_id), str(grant.project_id) if grant.project_id else None, grant.caller_subject_hash)
        )

    removed_actions = [action for action, capability in ACTION_CAPABILITY.items() if capability in removed]
    affected_run_ids: list[str] = []
    if removed_actions:
        commands = MobileCommand.objects.filter(
            display_run__agent=agent,
            action__in=removed_actions,
            status__in=[
                MobileCommand.STATUS_PENDING_APPROVAL,
                MobileCommand.STATUS_QUEUED,
                MobileCommand.STATUS_RUNNING,
            ],
        )
        affected_run_ids = [
            str(run_id)
            for run_id in commands.exclude(display_run_id__isnull=True).values_list("display_run_id", flat=True)
        ]
        commands.update(
            status=MobileCommand.STATUS_CANCELED,
            completed_at=now,
            updated_at=now,
        )

    subject_filter = Q()
    for tenant_id, project_id, subject_hash in changed_subjects:
        scope = Q(
            run__consumer_tenant_id=tenant_id,
            run__caller_subject_hash=subject_hash,
        )
        scope &= Q(run__consumer_project_id=project_id) if project_id else Q(run__consumer_project__isnull=True)
        subject_filter |= scope
    lease_filter = Q(run_id__in=affected_run_ids) if affected_run_ids else Q()
    if subject_filter:
        lease_filter |= subject_filter
    if lease_filter:
        AgentMobileLease.objects.filter(
            Q(run__agent=agent) & lease_filter,
            status=SoftDeleteModel.STATUS_ACTIVE,
        ).update(
            status=SoftDeleteModel.STATUS_DELETED,
            deleted_at=now,
            updated_at=now,
        )


def apply_mobile_policy(
    *,
    agent: Agent,
    requirement: str,
    capabilities: Iterable[str] | None,
    source: str,
) -> set[str]:
    """Persist the effective policy and immediately enforce any reduction."""

    if source not in {Agent.MOBILE_POLICY_SDK, Agent.MOBILE_POLICY_CLOUD}:
        raise exceptions.ValidationError({"mobile_policy_source": "Unsupported Mobile policy source."})
    if requirement not in {Agent.MOBILE_REQUIRED, Agent.MOBILE_OPTIONAL, Agent.MOBILE_DISABLED}:
        raise exceptions.ValidationError({"mobile_requirement": "Unsupported Mobile requirement."})
    normalized = normalize_mobile_capabilities(capabilities)
    if requirement == Agent.MOBILE_DISABLED:
        normalized = []
    if requirement == Agent.MOBILE_REQUIRED and not normalized:
        raise exceptions.ValidationError({
            "mobile_capabilities": "A Mobile-required Agent must declare at least one capability."
        })

    _, previous = current_mobile_declaration(agent)
    removed = set(previous) - set(normalized)
    agent.mobile_requirement = requirement
    agent.mobile_capabilities = normalized
    agent.mobile_policy_source = source
    agent.save(update_fields=[
        "mobile_requirement",
        "mobile_capabilities",
        "mobile_policy_source",
        "updated_at",
    ])
    _reconcile_mobile_policy_reduction(agent=agent, removed=removed)
    return removed


def _bindable_agent(*, request, agent_id: str) -> Agent:
    from .services import _get_bindable_agent

    return _get_bindable_agent(request=request, agent_id=agent_id)


def _scope_filter(request) -> Q:
    project_id = getattr(request, "project_id", None) or None
    return Q(project_id=project_id) if project_id else Q(project__isnull=True)


def list_mobile_bindings(*, request, agent_id: str):
    tenant = get_tenant_from_request(request)
    subject = request_subject(request)
    agent = _bindable_agent(request=request, agent_id=agent_id)
    return (
        AgentMobileBinding.objects.filter(
            tenant=tenant,
            agent=agent,
            caller_subject_hash=subject.subject_hash,
        )
        .filter(_scope_filter(request))
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .select_related("device")
        .order_by("-is_default", "-created_at")
    )


@transaction.atomic
def create_mobile_binding(*, request, agent_id: str, device_id: str, is_default: bool = True) -> AgentMobileBinding:
    tenant = get_tenant_from_request(request)
    subject = request_subject(request)
    agent = _bindable_agent(request=request, agent_id=agent_id)
    device = get_mobile_device(request=request, device_id=device_id)
    project_id = getattr(request, "project_id", None) or None
    if device.tenant_id != tenant.id or device.owner_subject_hash != subject.subject_hash:
        raise MobileNotFound("Mobile device not found.")
    if device.project_id and str(device.project_id) != str(project_id or ""):
        raise MobileNotFound("Mobile device not found.")
    if is_default:
        AgentMobileBinding.objects.filter(
            tenant=tenant,
            project_id=project_id,
            agent=agent,
            caller_subject_hash=subject.subject_hash,
            is_default=True,
            status=SoftDeleteModel.STATUS_ACTIVE,
        ).update(is_default=False)
    binding, _ = AgentMobileBinding.objects.update_or_create(
        tenant=tenant,
        project_id=project_id,
        agent=agent,
        device=device,
        caller_subject_hash=subject.subject_hash,
        defaults={
            "caller_principal_type": subject.principal_type,
            "is_default": is_default,
            "status": SoftDeleteModel.STATUS_ACTIVE,
            "deleted_at": None,
        },
    )
    return binding


@transaction.atomic
def update_mobile_binding(*, request, agent_id: str, binding_id: str, is_default: bool) -> AgentMobileBinding:
    binding = list_mobile_bindings(request=request, agent_id=agent_id).select_for_update().filter(id=binding_id).first()
    if binding is None:
        raise MobileNotFound("Mobile binding not found.")
    if is_default:
        AgentMobileBinding.objects.filter(
            tenant=binding.tenant,
            project=binding.project,
            agent=binding.agent,
            caller_subject_hash=binding.caller_subject_hash,
            is_default=True,
            status=SoftDeleteModel.STATUS_ACTIVE,
        ).exclude(id=binding.id).update(is_default=False)
    binding.is_default = is_default
    binding.save(update_fields=["is_default", "updated_at"])
    return binding


@transaction.atomic
def delete_mobile_binding(*, request, agent_id: str, binding_id: str) -> None:
    binding = list_mobile_bindings(request=request, agent_id=agent_id).select_for_update().filter(id=binding_id).first()
    if binding is None:
        raise MobileNotFound("Mobile binding not found.")
    binding.commands.filter(
        status__in=[MobileCommand.STATUS_PENDING_APPROVAL, MobileCommand.STATUS_QUEUED, MobileCommand.STATUS_RUNNING]
    ).update(status=MobileCommand.STATUS_CANCELED, completed_at=timezone.now(), updated_at=timezone.now())
    AgentMobileLease.objects.filter(
        run__mobile_binding=binding,
        status=SoftDeleteModel.STATUS_ACTIVE,
    ).update(status=SoftDeleteModel.STATUS_DELETED, deleted_at=timezone.now(), updated_at=timezone.now())
    binding.delete()


def _grant_queryset(*, request, agent: Agent):
    tenant = get_tenant_from_request(request)
    subject = request_subject(request)
    return AgentMobileGrant.objects.filter(
        tenant=tenant,
        agent=agent,
        caller_subject_hash=subject.subject_hash,
    ).filter(_scope_filter(request))


def get_mobile_grant(*, request, agent: Agent) -> AgentMobileGrant | None:
    return _grant_queryset(request=request, agent=agent).first()


@transaction.atomic
def set_mobile_grant(*, request, agent: Agent, scopes: Iterable[str]) -> AgentMobileGrant:
    requested = normalize_mobile_capabilities(scopes)
    _, published_capabilities = published_mobile_declaration(agent)
    declared = set(published_capabilities)
    if not set(requested).issubset(declared):
        raise exceptions.ValidationError({"scopes": "Grant scopes must be declared by the Agent."})
    tenant = get_tenant_from_request(request)
    subject = request_subject(request)
    project_id = getattr(request, "project_id", None) or None
    grant = _grant_queryset(request=request, agent=agent).select_for_update().first()
    if grant is None:
        grant = AgentMobileGrant(
            tenant=tenant,
            project_id=project_id,
            agent=agent,
            caller_subject_hash=subject.subject_hash,
            caller_principal_type=subject.principal_type,
        )
    grant.scopes = requested
    grant.status = SoftDeleteModel.STATUS_ACTIVE
    grant.deleted_at = None
    grant.granted_at = timezone.now()
    grant.revoked_at = None
    grant.save()
    return grant


@transaction.atomic
def revoke_mobile_grant(*, request, agent: Agent) -> None:
    grant = _grant_queryset(request=request, agent=agent).select_for_update().first()
    if grant is None:
        return
    grant.scopes = []
    grant.status = SoftDeleteModel.STATUS_DISABLED
    grant.revoked_at = timezone.now()
    grant.save(update_fields=["scopes", "status", "revoked_at", "updated_at"])
    MobileCommand.objects.filter(
        display_run__agent=agent,
        display_run__consumer_tenant=grant.tenant,
        caller_subject_hash=grant.caller_subject_hash,
        status__in=[MobileCommand.STATUS_PENDING_APPROVAL, MobileCommand.STATUS_QUEUED, MobileCommand.STATUS_RUNNING],
    ).update(status=MobileCommand.STATUS_CANCELED, completed_at=timezone.now(), updated_at=timezone.now())
    AgentMobileLease.objects.filter(
        run__agent=agent,
        run__consumer_tenant=grant.tenant,
        run__consumer_project=grant.project,
        run__caller_subject_hash=grant.caller_subject_hash,
        status=SoftDeleteModel.STATUS_ACTIVE,
    ).update(status=SoftDeleteModel.STATUS_DELETED, deleted_at=timezone.now(), updated_at=timezone.now())


def effective_mobile_capabilities(*, request, agent: Agent) -> list[str]:
    _, declared = published_mobile_declaration(agent)
    return effective_declared_mobile_capabilities(
        request=request,
        agent=agent,
        declared_capabilities=declared,
    )


def effective_declared_mobile_capabilities(
    *, request, agent: Agent, declared_capabilities: Iterable[str]
) -> list[str]:
    declared = set(normalize_mobile_capabilities(declared_capabilities))
    grant = get_mobile_grant(request=request, agent=agent)
    granted = set(grant.scopes or []) if grant and grant.status == SoftDeleteModel.STATUS_ACTIVE else set()
    return [value for value in MOBILE_CAPABILITIES if value in declared and value in granted]


def published_mobile_declaration(agent: Agent) -> tuple[str, list[str]]:
    """Compatibility alias for the current control-plane declaration."""

    return current_mobile_declaration(agent)


def runtime_mobile_declaration(runtime) -> tuple[str, list[str]]:
    """Resolve the policy to snapshot into a newly-created invocation Run."""

    return current_mobile_declaration(runtime.agent)


def effective_mobile_capabilities_for_run(run: AgentDisplayRun) -> list[str]:
    declared = set(normalize_mobile_capabilities(run.mobile_capabilities_snapshot))
    queryset = AgentMobileGrant.objects.filter(
        tenant=run.consumer_tenant,
        agent=run.agent,
        caller_subject_hash=run.caller_subject_hash,
        status=SoftDeleteModel.STATUS_ACTIVE,
    )
    queryset = queryset.filter(project=run.consumer_project) if run.consumer_project_id else queryset.filter(project__isnull=True)
    grant = queryset.first()
    granted = set(grant.scopes or []) if grant else set()
    return [value for value in MOBILE_CAPABILITIES if value in declared and value in granted]


def resolve_invocation_mobile_binding(
    *,
    request,
    agent: Agent,
    tenant: Tenant,
    mobile_capabilities: list[str],
    mobile_requirement: str | None = None,
    required_capabilities: list[str] | None = None,
) -> AgentMobileBinding | None:
    requirement = mobile_requirement or agent.mobile_requirement
    if requirement == Agent.MOBILE_DISABLED:
        return None
    subject = request_subject(request)
    project_id = getattr(request, "project_id", None) or None
    requested_id = str(request.headers.get("X-Nexus-Agent-Mobile-Binding") or "").strip()
    queryset = AgentMobileBinding.objects.filter(
        tenant=tenant,
        project_id=project_id,
        agent=agent,
        caller_subject_hash=subject.subject_hash,
        status=SoftDeleteModel.STATUS_ACTIVE,
        device__owner_subject_hash=subject.subject_hash,
        device__status=SoftDeleteModel.STATUS_ACTIVE,
    ).select_related("device")
    binding = queryset.filter(id=requested_id).first() if requested_id else queryset.filter(is_default=True).first()
    if requested_id and binding is None:
        raise MobileNotFound("Mobile binding not found.")
    if binding is None:
        if requirement == Agent.MOBILE_REQUIRED:
            raise AgentMobileRequired()
        return None
    required = set(normalize_mobile_capabilities(required_capabilities or agent.mobile_capabilities))
    if requirement == Agent.MOBILE_REQUIRED and not required.issubset(set(mobile_capabilities)):
        raise AgentMobilePermissionRequired()
    if not mobile_capabilities:
        if requirement == Agent.MOBILE_REQUIRED:
            raise AgentMobilePermissionRequired()
        return None
    if binding.device.lifecycle_status != MobileDevice.LIFECYCLE_ONLINE:
        if requirement == Agent.MOBILE_REQUIRED:
            raise AgentMobileUnavailable()
        return None
    return binding


@transaction.atomic
def acquire_mobile_lease(*, run: AgentDisplayRun) -> AgentMobileLease:
    if run.mobile_binding_id is None:
        raise AgentMobileUnavailable()
    now = timezone.now()
    AgentMobileLease.objects.filter(
        device=run.mobile_binding.device,
        status=SoftDeleteModel.STATUS_ACTIVE,
        expires_at__lte=now,
    ).update(status=SoftDeleteModel.STATUS_DELETED, deleted_at=now, updated_at=now)
    existing = AgentMobileLease.objects.filter(run=run, status=SoftDeleteModel.STATUS_ACTIVE).first()
    if existing:
        existing.expires_at = now + timedelta(minutes=15)
        existing.save(update_fields=["expires_at", "updated_at"])
        return existing
    try:
        return AgentMobileLease.objects.create(
            device=run.mobile_binding.device,
            run=run,
            expires_at=now + timedelta(minutes=15),
        )
    except IntegrityError as exc:
        raise AgentMobileBusy() from exc


def release_mobile_lease(*, run: AgentDisplayRun) -> None:
    AgentMobileLease.objects.filter(run=run, status=SoftDeleteModel.STATUS_ACTIVE).update(
        status=SoftDeleteModel.STATUS_DELETED,
        deleted_at=timezone.now(),
        updated_at=timezone.now(),
    )


def close_mobile_run(*, run: AgentDisplayRun) -> None:
    now = timezone.now()
    run.mobile_commands.filter(
        status__in=[
            MobileCommand.STATUS_PENDING_APPROVAL,
            MobileCommand.STATUS_QUEUED,
            MobileCommand.STATUS_RUNNING,
        ],
    ).update(
        status=MobileCommand.STATUS_CANCELED,
        completed_at=now,
        updated_at=now,
    )
    release_mobile_lease(run=run)


def issue_mobile_delegate_token(*, capabilities: list[str]) -> tuple[str, str, timezone.datetime | None]:
    if not capabilities:
        return "", "", None
    token = secrets.token_urlsafe(32)
    expires_at = timezone.now() + timedelta(hours=1)
    return token, hash_token(token), expires_at


def _delegate_run(*, run_id: str, token: str, capability: str = "") -> AgentDisplayRun:
    run = (
        AgentDisplayRun.objects.select_related("mobile_binding", "mobile_binding__device", "agent")
        .filter(id=run_id, run_kind=AgentDisplayRun.KIND_INVOCATION)
        .first()
    )
    if (
        run is None
        or run.status != AgentDisplayRun.STATUS_RUNNING
        or not run.mobile_binding_id
        or not token
        or not run.mobile_delegate_token_hash
        or not secrets.compare_digest(run.mobile_delegate_token_hash, hash_token(token))
        or run.mobile_delegate_token_expires_at is None
        or run.mobile_delegate_token_expires_at <= timezone.now()
        or run.mobile_binding.status != SoftDeleteModel.STATUS_ACTIVE
        or run.mobile_binding.device.status != SoftDeleteModel.STATUS_ACTIVE
        or run.mobile_binding.caller_subject_hash != run.caller_subject_hash
        or run.mobile_binding.device.owner_subject_hash != run.caller_subject_hash
        or not device_context_valid(run.mobile_binding.device)
    ):
        raise MobileNotFound("Mobile Run not found.")
    effective = effective_mobile_capabilities_for_run(run)
    if run.mobile_capabilities_snapshot and not effective:
        raise MobileNotFound("Mobile Run not found.")
    if capability and capability not in effective:
        raise AgentMobilePermissionRequired()
    if run.mobile_binding.device.lifecycle_status != MobileDevice.LIFECYCLE_ONLINE:
        raise AgentMobileUnavailable()
    return run


def mobile_delegate_status(*, run_id: str, token: str) -> dict[str, object]:
    run = _delegate_run(run_id=run_id, token=token)
    device = run.mobile_binding.device
    from apps.mobile.control import available_actions
    capabilities = effective_mobile_capabilities_for_run(run)
    return {
        "enabled": True,
        "available": device.lifecycle_status == MobileDevice.LIFECYCLE_ONLINE,
        "platform": device.platform,
        "status": device.lifecycle_status,
        "capabilities": capabilities,
        "supported_actions": [action for action in available_actions(device)
                              if ACTION_CAPABILITY.get(action) in capabilities],
    }


@transaction.atomic
def create_mobile_delegate_command(*, run_id: str, token: str, data: dict[str, object]) -> dict[str, object]:
    serializer = MobileCommandCreateSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data
    action = str(values["action"])
    arguments = dict(values.get("arguments") or {})
    client_request_id = values.get("client_request_id")
    capability = ACTION_CAPABILITY[action]
    run = _delegate_run(run_id=run_id, token=token, capability=capability)
    if client_request_id is not None:
        existing = MobileCommand.objects.filter(
            display_run=run,
            client_request_id=client_request_id,
        ).first()
        if existing is not None:
            if existing.action != action or dict(existing.arguments or {}) != arguments:
                raise AgentMobileCommandIdempotencyConflict()
            return _mobile_command_payload(existing)
    acquire_mobile_lease(run=run)
    try:
        # The savepoint keeps the outer transaction usable if a concurrent
        # retry wins the unique (Run, request ID) constraint first.
        with transaction.atomic():
            command = create_mobile_command_for_device(
                device=run.mobile_binding.device,
                action=action,
                arguments=arguments,
                ttl_seconds=min(max(int(values.get("ttl_seconds") or 120), 5), 900),
                display_run=run,
                mobile_binding=run.mobile_binding,
                caller_subject_hash=run.caller_subject_hash,
                client_request_id=client_request_id,
                hosted_agent=True,
            )
    except IntegrityError:
        if client_request_id is None:
            raise
        existing = MobileCommand.objects.filter(
            display_run=run,
            client_request_id=client_request_id,
        ).first()
        if existing is None:
            raise
        if existing.action != action or dict(existing.arguments or {}) != arguments:
            raise AgentMobileCommandIdempotencyConflict()
        return _mobile_command_payload(existing)
    if command.requires_approval:
        if run.interaction_mode not in {"stream", "task"}:
            raise AgentMobileInteractiveTransportRequired()
        interaction = AgentRunInteraction.objects.create(
            run=run,
            key=f"mobile:{command.id}",
            kind=AgentRunInteraction.KIND_CONFIRM,
            prompt=f"Allow this Agent to perform a high-risk Mobile action: {command.get_action_display()}?",
            choices_json=[
                {"value": "approve", "label": "Allow once"},
                {"value": "reject", "label": "Reject"},
            ],
            expires_at=min(command.expires_at, timezone.now() + timedelta(minutes=15)),
        )
        from .services import append_display_event

        append_display_event(
            run=run,
            event_type="CUSTOM",
            payload={
                "name": "nexus.mobile.input_required",
                "value": {
                    "interaction_id": str(interaction.id),
                    "command_id": str(command.id),
                    "action": command.action,
                    "risk_level": command.risk_level,
                },
            },
        )
        try:
            task = run.execution_task
        except AgentExecutionTask.DoesNotExist:
            task = None
        if task is not None:
            task.status = AgentExecutionTask.STATUS_INPUT_REQUIRED
            task.save(update_fields=["status", "updated_at"])
    return _mobile_command_payload(command)


@transaction.atomic
def _reconcile_mobile_approval(command: MobileCommand) -> MobileCommand:
    if command.status != MobileCommand.STATUS_PENDING_APPROVAL or not command.display_run_id:
        return command
    interaction = AgentRunInteraction.objects.select_for_update().filter(
        run=command.display_run,
        key=f"mobile:{command.id}",
    ).first()
    if interaction is None:
        return command
    if interaction.status == AgentRunInteraction.STATUS_PENDING and interaction.expires_at <= timezone.now():
        interaction.status = AgentRunInteraction.STATUS_EXPIRED
        interaction.save(update_fields=["status", "updated_at"])
    value = str((interaction.response_json or {}).get("value") or "")
    if interaction.status == AgentRunInteraction.STATUS_ANSWERED and value == "approve":
        acquire_mobile_lease(run=command.display_run)
        command.status = MobileCommand.STATUS_QUEUED
        command.approved_at = interaction.answered_at or timezone.now()
        command.save(update_fields=["status", "approved_at", "updated_at"])
    elif interaction.status in {
        AgentRunInteraction.STATUS_ANSWERED,
        AgentRunInteraction.STATUS_CANCELLED,
        AgentRunInteraction.STATUS_EXPIRED,
    }:
        command.status = MobileCommand.STATUS_REJECTED
        command.completed_at = timezone.now()
        command.save(update_fields=["status", "completed_at", "updated_at"])
    return command


def mobile_delegate_command(*, run_id: str, command_id: str, token: str) -> dict[str, object]:
    run = _delegate_run(run_id=run_id, token=token)
    purge_expired_mobile_screenshots(tenant=run.consumer_tenant)
    command = run.mobile_commands.filter(id=command_id, caller_subject_hash=run.caller_subject_hash).first()
    if command is None:
        raise MobileNotFound("Mobile command not found.")
    command = _reconcile_mobile_approval(command)
    return _mobile_command_payload(command)


def _mobile_command_payload(command: MobileCommand) -> dict[str, object]:
    result = dict(command.result or {})
    if command.action == MobileCommand.ACTION_CAPTURE_SCREEN and command.screenshot:
        result["screenshot_base64"] = base64.b64encode(bytes(command.screenshot)).decode("ascii")
        result["content_type"] = command.screenshot_content_type or result.get("content_type") or "image/webp"
    return {
        "id": str(command.id),
        "action": command.action,
        "status": command.status,
        "risk_level": command.risk_level,
        "requires_approval": command.requires_approval,
        "result": result,
        "error": command.error,
        "expires_at": command.expires_at.isoformat() if command.expires_at else None,
        "completed_at": command.completed_at.isoformat() if command.completed_at else None,
    }
