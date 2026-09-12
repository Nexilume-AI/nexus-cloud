"""Journaled personal Router setup; never run remote I/O inside a DB transaction.

Only a verified CAS result can promote a provisional key and revoke the previous
one. Uncertain writes retain their operation fence for explicit reconciliation;
they are not silently retried with another command or credential.
"""
from datetime import timedelta
import hashlib
import json
import re
import tomllib
from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions
from apps.workspaces.connection_core import WorkspaceError, WorkspaceConfigConflict
from apps.workspaces.computer_runtime import enqueue_runtime_command, wait_for_runtime_command
from apps.workspaces.models import WorkspaceToolManagedProfile, ComputerRuntimeCommand
from apps.workspaces.models import ComputerRuntimeDevice
from apps.workspaces.tool_codec import _configure_router_api_with_token, _provider_token, dump_toml, tool_config_revision
from apps.workspaces.tool_runtime import require_tool_config_fencing, enqueue_tool_config_fenced
from apps.audit.services import write_audit_log
from .models import PersonalToolConfigOperation, PersonalRouterCredential
from .tool_profiles import get_profile

KNOWN_NO_WRITE = {"TOOL_CONFIG_CONFLICT", "TOOL_CONFIG_INVALID", "TOOL_CONFIG_TOO_LARGE",
    "TOOL_CONFIG_BUSY", "TOOL_CONFIG_UNSAFE_PATH", "COMPUTER_SCOPE_MISMATCH"}


def request_identity(request, data):
    if data.get('section') == 'agents':
        from .agent_tool_setup import normalized
        values = normalized(data)
        if not re.fullmatch(r'[0-9a-f]{64}', str(data.get('expected_revision',''))):
            raise WorkspaceError('Reload and review the configuration before applying it.')
        values['expected_revision'] = data['expected_revision']
        digest = hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()
        key = request.headers.get('Idempotency-Key','')
        if not isinstance(key,str) or len(key)>128:
            raise WorkspaceError('Idempotency-Key must contain at most 128 characters.')
        return hashlib.sha256((key or digest).encode()).hexdigest(),digest
    if data.get("section") != "api" or data.get("action") not in {"set_router", "rotate_api_credential"}:
        raise WorkspaceError("Only Router API setup is available in this distribution.")
    if not re.fullmatch(r"[0-9a-f]{64}", str(data.get("expected_revision", ""))):
        raise WorkspaceError("Reload and review the configuration before applying it.")
    if not data.get("router_id"):
        raise WorkspaceError("Select a Router before applying API setup.")
    normalized = {"action": data["action"], "router_id": str(data["router_id"]),
        "expected_revision": data["expected_revision"], "section": "api", "tool": "codex"}
    digest = hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()
    key = request.headers.get("Idempotency-Key", "")
    if not isinstance(key, str) or len(key) > 128:
        raise WorkspaceError("Idempotency-Key must contain at most 128 characters.")
    return hashlib.sha256((key or digest).encode()).hexdigest(), digest


def _existing(profile, key, digest):
    if profile is None:
        return None
    op = PersonalToolConfigOperation.objects.filter(profile=profile, request_key=key).first()
    if op is not None and op.request_digest != digest:
        raise WorkspaceConfigConflict("This request key was already used for different configuration changes.")
    return op


def _audit(request, op, action):
    write_audit_log(request=request, actor=request.user, tenant=op.profile.tenant, action=action,
        resource_type="workspace_connection", resource_id=op.profile.connection_id,
        after={"operation_id": str(op.pk), "state": op.state, "credential_created": op.credential_created})


def _lock_device(session, profile):
    device = ComputerRuntimeDevice.objects.select_for_update().filter(connection_id=profile.connection_id).first()
    if device is None or device.revoked_at is not None:
        raise WorkspaceConfigConflict("Computer pairing was revoked or replaced; pair it again before configuring tools.")
    session.connection.runtime_device = device
    require_tool_config_fencing(session.connection)
    return device


@transaction.atomic
def prepare(*, request, session, data, content, parsed, prior_profile, key, digest):
    from . import tool_setup
    # get_profile serializes first-create on the Computer. An active operation
    # persists across process restarts and blocks other API/Agent profile writes.
    profile = get_profile(request=request, session_id=session.pk, create=True)
    existing = _existing(profile, key, digest)
    if existing:
        return existing
    if tool_config_revision(content) != data["expected_revision"]:
        raise WorkspaceConfigConflict()
    if PersonalToolConfigOperation.objects.filter(profile=profile, active=True).exists():
        raise WorkspaceConfigConflict("Another Tool Setup change is pending; retry its original request before making new changes.")
    if profile.config_revision != (prior_profile.config_revision if prior_profile else ""):
        raise WorkspaceConfigConflict()
    device = _lock_device(session, profile)
    if data.get('section') == 'agents':
        from .agent_tool_setup import prepare as prepare_agents
        updated, fields = prepare_agents(request, profile, parsed, data)
        target = dump_toml(updated)
        try:
            valid = tomllib.loads(target) == updated and len(target.encode()) <= tool_setup.MAX_CONFIG_BYTES
        except tomllib.TOMLDecodeError:
            valid = False
        if not valid:
            raise WorkspaceError('Existing configuration cannot be safely preserved; simplify it on the Computer.')
        from apps.common.crypto import encrypt_secret
        op = PersonalToolConfigOperation.objects.create(profile=profile,request_key=key,request_digest=digest,
            action=data['action'],expected_revision=data['expected_revision'],target_revision=tool_config_revision(target),
            profile_revision=profile.config_revision,old_credential=profile.router_credential,
            encrypted_before_config=encrypt_secret(content),**fields)
        op.command=enqueue_tool_config_fenced(connection=session.connection,
            payload={'content':target,'expected_revision':data['expected_revision']},
            idempotency_key='personal-tool-'+op.pk.hex)
        op.save(update_fields=['command'])
        _audit(request,op,'workspaces.tool_setup.prepared')
        return op
    router, models = tool_setup._router(request, data["router_id"])
    state, _ = tool_setup._api_state(request, session.pk, parsed, profile)
    reuse = data["action"] == "set_router" and state == "ready" and profile.router_id == router.pk
    if reuse:
        credential = profile.router_credential
        target = content
    else:
        from .router_credentials import export_router_credentials
        exported = export_router_credentials(request=request, router_id=router.pk)
        credential = PersonalRouterCredential.objects.select_for_update().get(pk=exported["gateway_api_key_id"])
        # A process death before verification cannot leave an orphan 90-day key.
        credential.expires_at = timezone.now() + timedelta(minutes=5)
        credential.issued_for = "tool_setup"
        credential.tool_connection_id = profile.connection_id
        credential.tool_device_id = device.pk
        credential.tool_profile_id = profile.pk
        credential.save(update_fields=["expires_at", "issued_for", "tool_connection_id", "tool_device_id", "tool_profile_id"])
        updated = _configure_router_api_with_token(request=request, config=parsed, router=router,
            models=models, token=exported["gateway_api_key"])
        target = dump_toml(updated)
        # Reject unsupported TOML values rather than changing external servers.
        try:
            roundtrip = tomllib.loads(target)
        except tomllib.TOMLDecodeError:
            raise WorkspaceError("Existing configuration cannot be safely preserved; simplify it on the Computer.") from None
        if roundtrip != updated or len(target.encode()) > tool_setup.MAX_CONFIG_BYTES:
            raise WorkspaceError("Existing configuration cannot be safely preserved; simplify it on the Computer.")
    from apps.common.crypto import encrypt_secret
    op = PersonalToolConfigOperation.objects.create(profile=profile, request_key=key, request_digest=digest,
        action=data["action"], expected_revision=data["expected_revision"], target_revision=tool_config_revision(target),
        profile_revision=profile.config_revision, old_credential=profile.router_credential,
        credential=credential, credential_created=not reuse, encrypted_before_config=encrypt_secret(content))
    op.command = enqueue_tool_config_fenced(connection=session.connection,
        payload={"content": target, "expected_revision": data["expected_revision"]},
        idempotency_key="personal-tool-" + op.pk.hex)
    op.save(update_fields=["command"])
    _audit(request, op, "workspaces.tool_setup.prepared")
    return op


@transaction.atomic
def fail_known(*, request, session_id, op_id, code):
    profile = get_profile(request=request, session_id=session_id)
    op = PersonalToolConfigOperation.objects.select_for_update().get(pk=op_id, profile=profile)
    if not op.active:
        return
    if op.state == "recovering":
        raise WorkspaceConfigConflict("Recovery owns this operation; an older write result cannot close it.")
    command = ComputerRuntimeCommand.objects.select_for_update().filter(pk=op.command_id).first()
    if (code not in KNOWN_NO_WRITE or command is None or
            command.status != ComputerRuntimeCommand.STATUS_FAILED or command.error_code != code):
        raise WorkspaceConfigConflict("The Computer has not confirmed a no-write failure; recovery is required.")
    if op.section == 'agents':
        from .agent_tool_setup import revoke_prepared
        revoke_prepared(op)
    elif op.credential_created:
        PersonalRouterCredential.objects.filter(pk=op.credential_id, revoked_at__isnull=True).update(revoked_at=timezone.now())
    op.state, op.active, op.error_code, op.completed_at = "failed", False, code, timezone.now()
    op.save(update_fields=["state", "active", "error_code", "completed_at"])
    _audit(request, op, "workspaces.tool_setup.failed")


@transaction.atomic
def finish(*, request, session_id, op_id, content, parsed, recovery=False):
    from . import tool_setup
    profile = get_profile(request=request, session_id=session_id)
    from apps.workspaces.execution import get_terminal_session
    _lock_device(get_terminal_session(request=request, session_id=session_id), profile)
    op = PersonalToolConfigOperation.objects.select_for_update().get(pk=op_id, profile=profile)
    if op.state == "applied":
        return op
    if not op.active or op.command_id is None:
        raise WorkspaceConfigConflict("Configuration operation is no longer applicable.")
    if op.state == "recovering" and not recovery:
        raise WorkspaceConfigConflict("Explicit recovery is in progress; continue the recovery request.")
    command = ComputerRuntimeCommand.objects.select_for_update().get(pk=op.fence_command_id if recovery else op.command_id)
    if recovery:
        from .tool_recovery import verify_barrier
        verify_barrier(op, command, content)
    if command.status != ComputerRuntimeCommand.STATUS_SUCCEEDED or tool_config_revision(content) != op.target_revision:
        raise WorkspaceConfigConflict("Configuration verification changed; the previous credential is still retained.")
    if profile.config_revision != op.profile_revision or profile.router_credential_id != op.old_credential_id:
        raise WorkspaceConfigConflict("The managed profile changed while the Computer was being configured.")
    if op.section == 'agents':
        from .agent_tool_setup import finish as finish_agents
        finish_agents(request,op,profile,parsed)
        profile.config_revision,profile.last_applied_at=op.target_revision,timezone.now()
        profile.save(update_fields=['config_revision','last_applied_at','updated_at'])
        op.state,op.active,op.completed_at='applied',False,timezone.now()
        op.save(update_fields=['state','active','completed_at'])
        _audit(request,op,'workspaces.tool_setup.applied')
        return op
    credential = PersonalRouterCredential.objects.select_for_update().filter(pk=op.credential_id,
        owner=request.user, tenant=profile.tenant, project=profile.project, revoked_at__isnull=True,
        expires_at__gt=timezone.now()).first()
    if credential is None:
        raise WorkspaceConfigConflict("The provisional credential expired or was revoked; recovery is required.")
    from .tool_credentials import binding_valid
    if credential.issued_for != "tool_setup" or not binding_valid(credential, require_applied=False):
        raise WorkspaceConfigConflict("Credential Computer binding is no longer valid.")
    router, models = tool_setup._router(request, credential.router_id)
    if (set(models) != set(credential.model_names) or
            hashlib.sha256(_provider_token(parsed).encode()).hexdigest() != credential.token_hash):
        raise WorkspaceConfigConflict("Router model permissions changed; reload and recover the pending operation.")
    if op.credential_created:
        credential.expires_at = timezone.now() + timedelta(days=90)
        credential.save(update_fields=["expires_at"])
    profile.router, profile.router_credential, profile.provider_runtime = router, credential, None
    profile.config_revision, profile.last_applied_at = op.target_revision, timezone.now()
    profile.save(update_fields=["router", "router_credential", "provider_runtime", "config_revision", "last_applied_at", "updated_at"])
    if op.old_credential_id and op.old_credential_id != credential.pk:
        PersonalRouterCredential.objects.filter(pk=op.old_credential_id, owner=request.user,
            issued_for="tool_setup", tool_profile_id=profile.pk,
            revoked_at__isnull=True).update(revoked_at=timezone.now())
    op.state, op.active, op.completed_at = "applied", False, timezone.now()
    op.save(update_fields=["state", "active", "completed_at"])
    _audit(request, op, "workspaces.tool_setup.applied")
    return op


def apply(*, request, session_id, data):
    from . import tool_setup
    session = tool_setup._session(request, session_id, data.get("tool", "codex"))
    require_tool_config_fencing(session.connection)
    key, digest = request_identity(request, data)
    profile = get_profile(request=request, session_id=session_id)
    op = _existing(profile, key, digest)
    if op is None:
        _, content, parsed = tool_setup._read(session)
        if tool_config_revision(content) != data["expected_revision"]:
            raise WorkspaceConfigConflict()
        op = prepare(request=request, session=session, data=data, content=content, parsed=parsed,
            prior_profile=profile, key=key, digest=digest)
    if op.state in {"failed", "revoked", "rolled_back", "kept_local"}:
        raise WorkspaceConfigConflict("This operation failed without being applied. Reload and review; use a new Idempotency-Key for an explicit retry.")
    if op.state == "recovering":
        raise WorkspaceConfigConflict("Continue the pending recovery before applying another change.")
    if op.state != "applied":
        if op.command_id is None:
            raise WorkspaceConfigConflict("The write receipt is unavailable; recover the pending operation before changing credentials.")
        try:
            wait_for_runtime_command(op.command, timeout_seconds=32)
        except exceptions.APIException:
            op.command.refresh_from_db()
            if op.command.status == ComputerRuntimeCommand.STATUS_FAILED and op.command.error_code in KNOWN_NO_WRITE:
                fail_known(request=request, session_id=session_id, op_id=op.pk, code=op.command.error_code)
                raise WorkspaceConfigConflict("Computer rejected this write; the previous managed credential was retained.") from None
            # A timeout/cancel/disconnect does not prove that an external write
            # did not occur. Keep the fence and old key; never dispatch again.
            PersonalToolConfigOperation.objects.filter(pk=op.pk, active=True, state__in=["writing", "uncertain"]).update(state="uncertain", error_code="TOOL_CONFIG_RECOVERY_REQUIRED")
            raise WorkspaceConfigConflict("Computer write is unconfirmed. Retry the original request to inspect its receipt; the previous credential remains valid.") from None
        _, content, parsed = tool_setup._read(session)
        op = finish(request=request, session_id=session_id, op_id=op.pk, content=content, parsed=parsed)
    result = tool_setup.get_workspace_tool_config(request=request, session_id=session_id)
    result["operation"] = {"id": str(op.pk), "state": op.state, "credential_action": "created" if op.credential_created else "reused"}
    return result
