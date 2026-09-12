"""Personal Agent-only MCP credentials with live Computer and owner checks.

Issuance is internal to Tool Setup. No endpoint exports an owner token or adopts
an external MCP server. A new key remains unusable until verified configuration
application binds it to the exact managed profile.
"""
import hashlib
import secrets
import uuid
from datetime import timedelta
from types import SimpleNamespace
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from rest_framework import exceptions
from apps.common.subjects import request_subject
from apps.workspaces.models import WorkspaceToolManagedProfile, ComputerRuntimeDevice
from .models import PersonalAgentCredential, PersonalInvocationUsage

PREFIX = 'np-agent-'
MCP_ROUTES = {'agent-mcp-proxy','agent-mcp-legacy-sse','agent-mcp-legacy-messages',
              'agent-legacy-sse','agent-legacy-messages'}


def _denied():
    raise exceptions.AuthenticationFailed('Agent credential is invalid, expired or revoked.')


def _profiles(row):
    request = SimpleNamespace(user=row.owner,tenant_id=str(row.tenant_id),project_id=str(row.project_id),
        META={},headers={})
    return WorkspaceToolManagedProfile.objects.filter(pk=row.tool_profile_id,tool='codex',profile='nexus',
        status='active',created_by_id=row.owner_id,tenant_id=row.tenant_id,project_id=row.project_id,
        connection_id=row.tool_connection_id,connection__status='active',connection__connection_type='runtime',
        connection__created_by_id=row.owner_id,connection__tenant_id=row.tenant_id,connection__project_id=row.project_id,
        connection__owner_subject_type='user',connection__owner_subject_hash=request_subject(request).subject_hash,
        connection__runtime_device__id=row.tool_device_id,connection__runtime_device__revoked_at__isnull=True)


def binding_valid(row, *, require_applied=True):
    query = _profiles(row)
    if require_applied:
        query = query.filter(agent_credential_id=row.pk)
    return query.exists()


def _current(request, credential_id):
    from .authentication import validate_owner
    row = PersonalAgentCredential.objects.select_related('owner').filter(pk=credential_id,
        revoked_at__isnull=True,expires_at__gt=timezone.now()).first()
    if row is None:
        _denied()
    user,tenant_id,project_id = validate_owner(request,row.owner)
    if str(row.tenant_id)!=tenant_id or str(row.project_id)!=project_id or not binding_valid(row):
        _denied()
    if (not isinstance(row.agent_ids,list) or not 1<=len(row.agent_ids)<=128 or
            any(not isinstance(value,str) for value in row.agent_ids)):
        _denied()
    return row,user


def current_credential(request):
    value = getattr(request,'_nexus_personal_agent_credential_id',None)
    return _current(request,value)[0] if value is not None else None


def enforce_agent(request, agent_id):
    row = current_credential(request)
    if row is not None and str(agent_id) not in row.agent_ids:
        raise exceptions.NotFound('Agent not found.')
    return row


def authenticate(request, token):
    try:
        selector, secret = token[len(PREFIX):].split('.')
        if len(selector)!=32 or len(secret)!=43:
            _denied()
        credential_id = uuid.UUID(hex=selector)
    except (ValueError,TypeError):
        _denied()
    stored = PersonalAgentCredential.objects.filter(pk=credential_id).values_list('token_hash',flat=True).first()
    if stored is None or not constant_time_compare(stored,hashlib.sha256(token.encode('ascii')).hexdigest()):
        _denied()
    row,user = _current(request,credential_id)
    match = getattr(request,'resolver_match',None)
    if (getattr(match,'url_name','') not in MCP_ROUTES or request.method not in {'GET','HEAD','POST','DELETE'} or
            request.headers.get('X-Nexus-End-User')):
        raise exceptions.PermissionDenied('This credential only permits its configured Agent MCP APIs.')
    if str(getattr(match,'kwargs',{}).get('agent_id','')) not in row.agent_ids:
        raise exceptions.NotFound('Agent not found.')
    request._nexus_personal_agent_credential_id = row.pk
    # Presence is not authority: an offline, non-revoked Computer's existing
    # config is still valid. Revocation/replacement is checked on every request.
    PersonalAgentCredential.objects.filter(pk=row.pk).filter(
        last_used_at__lt=timezone.now()-timedelta(minutes=1)).update(last_used_at=timezone.now())
    if row.last_used_at is None:
        PersonalAgentCredential.objects.filter(pk=row.pk,last_used_at__isnull=True).update(last_used_at=timezone.now())
    return user,row


@transaction.atomic
def issue(*, request, profile, agent_ids):
    from .authentication import validate_owner
    from apps.agents.services import get_agent
    from apps.workspaces.tool_runtime import require_tool_config_fencing
    user,tenant_id,project_id = validate_owner(request,request.user)
    if not isinstance(agent_ids,list) or not 1<=len(agent_ids)<=128:
        raise exceptions.ValidationError('Choose between one and 128 Agents.')
    try:
        ids = sorted({str(uuid.UUID(str(value))) for value in agent_ids})
    except (ValueError,TypeError,AttributeError):
        raise exceptions.ValidationError('Choose valid Agents.') from None
    for agent_id in ids:
        agent = get_agent(request=request,agent_id=agent_id)
        if agent.status!='active':
            raise exceptions.ValidationError('Only active Agents can be connected.')
    device = require_tool_config_fencing(profile.connection)
    ComputerRuntimeDevice.objects.select_for_update().get(pk=device.pk)
    credential_id = uuid.uuid4()
    token = PREFIX+credential_id.hex+'.'+secrets.token_urlsafe(32)
    row = PersonalAgentCredential(owner=user,tenant_id=tenant_id,project_id=project_id,
        pk=credential_id,agent_ids=ids,token_hash=hashlib.sha256(token.encode('ascii')).hexdigest(),
        tool_connection_id=profile.connection_id,tool_device_id=device.pk,tool_profile_id=profile.pk,
        expires_at=timezone.now()+timedelta(minutes=5))
    if not binding_valid(row,require_applied=False):
        raise exceptions.NotFound('Computer profile not found.')
    row.save(force_insert=True)
    from apps.audit.services import write_audit_log
    write_audit_log(request=request,actor=user,tenant=profile.tenant,action='workspaces.tool_setup.agent_credential_prepared',
        resource_type='workspace_connection',resource_id=profile.connection_id,
        after={'credential_id':str(row.pk),'agent_count':len(ids)})
    return row,token


def restore_invocation_authority(request, payload):
    # The unmodified shared Task enqueue creates admission before its encrypted
    # payload. Consult that durable record, not caller headers or a saved token.
    context = payload.get('context')
    follow_up = payload.get('invocation_authority')
    if context is None:
        if follow_up is None:
            return  # Legacy owner-only restoration; actual workers require context.
        context = follow_up
    try:
        if not isinstance(context,dict) or type(context.get('turn_index',1)) is not int:
            raise ValueError()
        run_id = uuid.UUID(context['run_id'])
        usage = PersonalInvocationUsage.objects.select_related('invocation').filter(run_id=run_id,
            turn_index=context.get('turn_index',1),tenant_id=request.tenant_id,
            invocation__actor_id=request.user.pk,invocation__display_run_id=run_id).first()
        if usage is None:
            raise ValueError()
        if follow_up is not None and (not isinstance(follow_up,dict) or str(usage.invocation_id)!=follow_up.get('id')):
            raise ValueError()
    except (ValueError,TypeError,KeyError):
        if follow_up is not None:
            from apps.agents.follow_ups import conflict
            conflict('FOLLOW_UP_CONTEXT_CHANGED')
        raise exceptions.PermissionDenied('Saved invocation authority is unavailable.') from None
    if usage.agent_credential_id is not None:
        request._nexus_personal_agent_credential_id = usage.agent_credential_id
        try:
            enforce_agent(request,usage.invocation.agent_id)
        except exceptions.APIException:
            raise exceptions.PermissionDenied('Task Agent credential is no longer authorized.') from None


def revoke_invalid_bindings(*, connection_id, using='default', force=None):
    with transaction.atomic(using=using):
        from apps.workspaces.models import WorkspaceConnection
        from .models import PersonalToolConfigOperation
        from .tool_credentials import _operation_command_ids, _cancel_commands
        WorkspaceConnection.objects.using(using).select_for_update().filter(pk=connection_id).first()
        rows = PersonalAgentCredential.objects.using(using).filter(tool_connection_id=connection_id,
            revoked_at__isnull=True).select_related('owner')
        ids = list(rows.filter(force).values_list('pk',flat=True)) if force is not None else [row.pk for row in rows if not binding_valid(row,require_applied=False)]
        if ids:
            PersonalAgentCredential.objects.using(using).filter(pk__in=ids,revoked_at__isnull=True).update(revoked_at=timezone.now())
            # Last-Agent removal has no new credential; the previous key must
            # still fence its pending Computer command on revocation.
            from django.db.models import Q
            operations=PersonalToolConfigOperation.objects.using(using).filter(active=True,section='agents').filter(
                Q(agent_credential_id__in=ids)|Q(old_agent_credential_id__in=ids))
            command_ids=_operation_command_ids(operations)
            operations.update(state='revoked',active=False,completed_at=timezone.now(),error_code='TOOL_COMPUTER_REVOKED')
            transaction.on_commit(lambda:_cancel_commands(command_ids,using),using=using)
            from apps.audit.services import write_audit_log
            row = PersonalAgentCredential.objects.using(using).select_related('tenant').get(pk=ids[0])
            write_audit_log(request=None,actor=None,tenant=row.tenant,
                action='workspaces.tool_setup.agent_credentials_revoked',resource_type='workspace_connection',
                resource_id=connection_id,after={'credential_count':len(ids)})
