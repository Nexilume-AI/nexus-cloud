"""Incremental Personal MCP configuration, composed into the fenced journal.

Profile metadata proves which names Nexus owns. No adoption by name prefix, no
external-server overwrite, and no owner token or commercial key is exported.
"""
import hashlib
import uuid
from datetime import timedelta
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from rest_framework import exceptions
from apps.workspaces.connection_core import WorkspaceConfigConflict, WorkspaceError
from apps.workspaces.tool_codec import copy_config_dict, _mcp_token
from .models import PersonalAgentCredential
from . import agent_credentials

ACTIONS = {'add_agent', 'remove_agent', 'rotate_agent_credential'}


def normalized(data):
    action = data.get('action')
    if data.get('section') != 'agents' or action not in ACTIONS:
        raise WorkspaceError('Choose an Agent setup action.')
    result = {'section':'agents', 'action':action, 'tool':'codex'}
    if action == 'add_agent':
        try:
            result['agent_id'] = str(uuid.UUID(str(data['agent_id'])))
        except (KeyError, TypeError, ValueError):
            raise WorkspaceError('Choose an Agent.') from None
    if action == 'remove_agent':
        name = data.get('mcp_server_name')
        if not isinstance(name,str) or not name or len(name)>128:
            raise WorkspaceError('Choose a connected Agent.')
        result['mcp_server_name'] = name
    return result


def mapping(profile):
    rows = profile.managed_mcp_servers if profile else {}
    if not isinstance(rows,dict) or len(rows)>128:
        raise WorkspaceConfigConflict('Managed MCP metadata needs repair.')
    seen = set()
    for name,row in rows.items():
        try:
            agent_id = str(uuid.UUID(row['agent_id']))
            if (not isinstance(row,dict) or set(row)!={'agent_id','url'} or
                    name != 'nexus-agent-'+uuid.UUID(agent_id).hex or
                    not isinstance(row['url'],str) or agent_id in seen):
                raise ValueError()
            seen.add(agent_id)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise WorkspaceConfigConflict('Historical MCP metadata cannot be adopted automatically.') from None
    return copy_config_dict(rows)


def server_row(url, token):
    return {'url':url, 'http_headers':{'Authorization':'Bearer '+token}}


def inspect(request, profile, parsed):
    """Internal state: plaintext is transient and never part of a public result."""
    managed = mapping(profile)
    servers = parsed.get('mcp_servers',{})
    if not managed:
        return managed, None, '', 'not_configured'
    credential = None
    if profile.agent_credential_id:
        try:
            credential,_ = agent_credentials._current(request,profile.agent_credential_id)
        except exceptions.APIException:
            pass
    tokens=[]
    for name,meta in managed.items():
        value=servers.get(name)
        if not isinstance(value,dict):
            return managed,credential,'','needs_repair'
        token=_mcp_token(value)
        if value != server_row(meta['url'],token):
            return managed,credential,'','needs_repair'
        tokens.append(token)
    if (credential is None or not tokens[0] or len(set(tokens))!=1 or
            set(credential.agent_ids)!={row['agent_id'] for row in managed.values()} or
            not constant_time_compare(credential.token_hash,hashlib.sha256(tokens[0].encode()).hexdigest())):
        return managed,credential,'','needs_repair'
    return managed,credential,tokens[0],'ready'


def agent_for_add(request, agent_id):
    from apps.agents.services import get_agent
    agent=get_agent(request=request,agent_id=agent_id)
    if agent.status!='active':
        raise WorkspaceError('Enable this Agent before connecting it.')
    # Same runtime resolution/availability check as actual MCP invocation.
    from apps.agents.runtime_services import get_runtime_deployment
    get_runtime_deployment(agent=agent,env='prod')
    return agent


def plan(request, profile, parsed, data):
    data=normalized(data)
    managed,credential,token,state=inspect(request,profile,parsed)
    servers=parsed.get('mcp_servers',{})
    target=copy_config_dict(managed)
    action=data['action']
    # Metadata alone never authorizes replacing a changed external endpoint or
    # command. Explicit Rotate may repair a missing/expired bearer at the same
    # exact managed URL; add/remove cannot silently rotate.
    for name,meta in managed.items():
        row=servers.get(name)
        if (not isinstance(row,dict) or row.get('url')!=meta['url'] or
                set(row)-{'url','http_headers'} or
                not isinstance(row.get('http_headers',{}),dict) or
                set(row.get('http_headers',{}))-{'Authorization'}):
            raise WorkspaceConfigConflict('A managed MCP server was changed outside Nexus. Restore its configuration before editing it.')
    if managed and state!='ready' and action!='rotate_agent_credential':
        raise WorkspaceConfigConflict('Agent credential needs repair. Review an explicit credential rotation first.')
    if action=='add_agent':
        agent=agent_for_add(request,data['agent_id'])
        name='nexus-agent-'+agent.pk.hex
        if name in servers and name not in managed:
            raise WorkspaceConfigConflict('An external MCP server already uses this name. Rename it on the Computer; Nexus will not overwrite it.')
        target[name]={'agent_id':str(agent.pk), 'url':request.build_absolute_uri(f'/api/v1/agents/{agent.pk}/mcp/')}
        if len(target)>128:
            raise WorkspaceError('Connect at most 128 Agents per profile.')
    elif action=='remove_agent':
        if data['mcp_server_name'] not in managed:
            raise WorkspaceConfigConflict('Only a Nexus-managed Agent can be removed here.')
        del target[data['mcp_server_name']]
    elif not managed:
        raise WorkspaceError('Connect an Agent before rotating its credential.')
    credential_action=('revoke' if not target else 'rotate' if action=='rotate_agent_credential'
        else 'reuse' if state=='ready' else 'create')
    return managed,target,credential,token,credential_action


def prepare(request, profile, parsed, data):
    previous,target,credential,token,action=plan(request,profile,parsed,data)
    created=action in {'create','rotate'}
    if created:
        credential,token=agent_credentials.issue(request=request,profile=profile,
            agent_ids=[row['agent_id'] for row in target.values()])
    if not target:
        credential=None
    updated=copy_config_dict(parsed)
    servers=updated.setdefault('mcp_servers',{})
    for name in previous:
        servers.pop(name,None)
    for name,meta in target.items():
        servers[name]=server_row(meta['url'],token)
    return updated,dict(section='agents',previous_managed_mcp=previous,target_managed_mcp=target,
        old_agent_credential_id=profile.agent_credential_id,agent_credential_id=getattr(credential,'pk',None),
        credential_created=created)


def check_profile(op, profile):
    if (profile.agent_credential_id!=op.old_agent_credential_id or
            profile.managed_mcp_servers!=op.previous_managed_mcp):
        raise WorkspaceConfigConflict('Managed Agent configuration changed while this operation was pending.')


def revoke_prepared(op):
    if op.credential_created:
        PersonalAgentCredential.objects.filter(pk=op.agent_credential_id,revoked_at__isnull=True).update(revoked_at=timezone.now())


def finish(request, op, profile, parsed):
    check_profile(op,profile)
    target=op.target_managed_mcp
    credential=None
    if target:
        credential=PersonalAgentCredential.objects.select_for_update().filter(pk=op.agent_credential_id,
            owner=request.user,tenant=profile.tenant,project=profile.project,revoked_at__isnull=True,
            expires_at__gt=timezone.now()).first()
        if credential is None or not agent_credentials.binding_valid(credential,require_applied=False):
            raise WorkspaceConfigConflict('Agent credential expired or its Computer binding changed. Recover this operation.')
        expected=target if op.credential_created else op.previous_managed_mcp
        if set(credential.agent_ids)!={meta['agent_id'] for meta in expected.values()}:
            raise WorkspaceConfigConflict('Agent permissions changed while configuration was pending.')
        from apps.agents.services import get_agent
        for meta in target.values():
            get_agent(request=request,agent_id=meta['agent_id'])
        if op.action=='add_agent':
            for name in set(target)-set(op.previous_managed_mcp):
                agent_for_add(request,target[name]['agent_id'])
        for name,meta in target.items():
            value=parsed.get('mcp_servers',{}).get(name,{})
            token=_mcp_token(value)
            if value!=server_row(meta['url'],token) or not constant_time_compare(credential.token_hash,hashlib.sha256(token.encode()).hexdigest()):
                raise WorkspaceConfigConflict('Agent credential verification failed.')
        # Existing keys do not gain new permissions until the physical write
        # has been verified. Removing a server contracts its allowlist here.
        credential.agent_ids=sorted({row['agent_id'] for row in target.values()})
        if op.credential_created:
            credential.expires_at=timezone.now()+timedelta(days=90)
        credential.save(update_fields=['agent_ids','expires_at'])
    profile.agent_credential=credential
    profile.managed_mcp_servers=target
    profile.save(update_fields=['agent_credential','managed_mcp_servers','updated_at'])
    if op.old_agent_credential_id and op.old_agent_credential_id!=getattr(credential,'pk',None):
        PersonalAgentCredential.objects.filter(pk=op.old_agent_credential_id,tool_profile_id=profile.pk,
            revoked_at__isnull=True).update(revoked_at=timezone.now())


def validate_backup(request, op, parsed):
    # Run for API recovery too: its unchanged MCP content may contain an old
    # Agent token that was revoked while the API operation was pending.
    for server in parsed.get('mcp_servers',{}).values():
        if not isinstance(server,dict):
            continue
        token=_mcp_token(server)
        if not token.startswith(agent_credentials.PREFIX):
            continue
        row=PersonalAgentCredential.objects.filter(token_hash=hashlib.sha256(token.encode()).hexdigest()).first()
        try:
            if row is None:
                raise ValueError()
            agent_credentials._current(request,row.pk)
        except (ValueError,exceptions.APIException):
            raise WorkspaceConfigConflict('The previous Agent credential is no longer valid; recovery cannot restore it.') from None


def summary(request, profile, parsed):
    try:
        managed,_,_,state=inspect(request,profile,parsed)
    except exceptions.APIException:
        managed,state={},'needs_repair'
    from apps.agents.models import Agent
    names=dict(Agent.objects.filter(id__in=[row['agent_id'] for row in managed.values()],
        tenant_id=request.tenant_id,created_by=request.user).values_list('id','name'))
    rows=[]
    for name,meta in managed.items():
        available,code,message=False,'AGENT_RUNTIME_UNAVAILABLE','Enable the Agent and restore its runtime.'
        if state=='ready':
            try:
                agent_for_add(request,meta['agent_id'])
            except exceptions.APIException:
                pass
            else:
                available,code,message=True,'',''
        else:
            code,message='AGENT_CREDENTIAL_NEEDS_REPAIR','Review an explicit Agent credential rotation.'
        rows.append({'server_name':name,'agent_id':meta['agent_id'],
            'name':names.get(uuid.UUID(meta['agent_id']),'Unavailable Agent'),'status':state,
            'url':meta['url'],'available':available,'code':code,'message':message})
    return {'status':state,'credential_status':state,'managed':rows,'code':'AGENT_CREDENTIAL_NEEDS_REPAIR' if state=='needs_repair' else '',
        'message':'Review credential rotation; changed external configuration is never overwritten.' if state=='needs_repair' else ''}


def preview(request, profile, parsed, data, revision, availability):
    previous,target,_,_,action=plan(request,profile,parsed,data)
    external_count=str(len(parsed.get('mcp_servers',{}))-len(previous))
    changes=[{'kind':'preserved','label':'API settings','before':'Current configuration','after':'Unchanged'},
        {'kind':'preserved','label':'External MCP servers','before':external_count,'after':external_count}]
    for name in dict.fromkeys([*previous,*target]):
        changes.append({'kind':'added' if name not in previous else 'removed' if name not in target else 'preserved',
            'label':name,'before':'Connected' if name in previous else 'Not connected',
            'after':'Connected' if name in target else 'Removed'})
    return {'tool':'codex','section':'agents','action':data['action'],'revision':revision,
        'changes':changes,'credential_action':action,'warnings':[] if availability['available'] else [availability],
        'destructive':False,'can_apply':availability['available']}
