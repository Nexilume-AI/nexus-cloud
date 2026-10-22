"""URL elicitation and caller-owned credentials, separate from all Run event stores."""
from datetime import timedelta
import json
from urllib.parse import urlsplit
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from rest_framework import exceptions
from apps.common.crypto import encrypt_secret, decrypt_secret
from apps.common.subjects import request_subject, hash_token
from .models import AgentCredentialAuthorization, AgentDisplayRun

from .credential_config import PROVIDERS, configuration


def enabled(run):
    allowed = getattr(settings, "NEXUS_AGENT_CREDENTIAL_ENABLED_AGENTS", ())
    if str(run.agent_id) not in allowed:
        raise exceptions.PermissionDenied("Provider authorization is not enabled for this Agent.")


def owner_id(run):
    if run.caller_principal_type == "user":
        return run.caller_principal_id
    # Personal Agent credentials authenticate as their verified owner.
    return None


def scope(run):
    from .caller_identity import principal_filter
    identity = principal_filter(run.caller_principal_type, run.caller_principal_id)
    if run.caller_principal_type not in ("api_key", "api_key_external"):
        identity["run__caller_subject_hash"] = run.caller_subject_hash
    return dict(run__agent_id=run.agent_id, run__consumer_tenant_id=run.consumer_tenant_id,
                run__consumer_project_id=run.consumer_project_id,
                **identity)


def grant_state(row):
    state = row.status if row.expires_at > timezone.now() else 'expired'
    if state == 'authorized':
        source = row.source_grant if row.source_grant_id else row
        if (source.status != 'authorized' or source.expires_at <= timezone.now() or not source.encrypted_secret
                or source.owner_id != row.owner_id or source.provider != row.provider or source.base_url != row.base_url):
            return 'revoked'
        if source.issued_key_id:
            # Router delegation credentials are not issued by this distribution.
            return 'revoked'
    return state


def public(row):
    base = str(getattr(settings, "NEXUS_PUBLIC_BASE_URL", "")).rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.query or parsed.fragment:
        raise exceptions.APIException("Secure authorization URL is not configured.")
    state = grant_state(row)
    return {"authorization_id": str(row.id), "status": state, "provider": row.provider,
            "provider_name": PROVIDERS[row.provider]["name"], "base_url": row.base_url,
            "model": row.model_name, "protocol": PROVIDERS[row.provider]["protocol"],
            "router_id": str(row.router_id or ''),
            "agent_name": row.run.agent.name, "agent_id": str(row.run.agent_id),
            "tenant_id": str(row.run.consumer_tenant_id), "project_id": str(row.run.consumer_project_id or ""),
            "caller_type": row.run.caller_principal_type,
            "expires_at": row.expires_at.isoformat(),
            "authorization_url": base + "/api/v1/agent-authorizations/" + str(row.id) + "/page/",
            "credential_supplied": state == "authorized", "preauthorized": row.preauthorized}


def internal_run(run_id, token):
    from .runtime_services import get_internal_interaction_run
    run = get_internal_interaction_run(run_id=str(run_id), token=token)
    enabled(run)
    if run.run_kind != AgentDisplayRun.KIND_INVOCATION or not run.consumer_tenant_id or not run.caller_subject_hash:
        raise exceptions.PermissionDenied("An authenticated caller Run is required.")
    return run


@transaction.atomic
def request_authorization(run_id, token, provider, base_url=None, model=None, force_new=False):
    run = internal_run(run_id, token)
    if run.status != AgentDisplayRun.STATUS_RUNNING:
        raise exceptions.PermissionDenied("Authorization requests require a running invocation.")
    from .credential_preauthorization import current
    configured = current(run)
    if configured is not None:
        if str(configured.owner_id) != str(owner_id(run)):
            raise exceptions.PermissionDenied('Invocation credential owner is no longer valid.')
        if grant_state(configured) != 'authorized':
            raise exceptions.PermissionDenied('Invocation preauthorization is no longer active.')
        # The caller's explicit MCP configuration takes priority over tool defaults.
        return public(configured)
    provider,base_url,model = configuration(provider,base_url,model)
    if type(force_new) is not bool:
        raise exceptions.ValidationError('force_new must be a boolean.')
    owner = owner_id(run)
    if not owner:
        raise exceptions.PermissionDenied("Use your personal Agent MCP credential or sign in; shared caller keys cannot authorize provider secrets.")
    existing = AgentCredentialAuthorization.objects.filter(
        **scope(run), owner_id=owner, preauthorized=False, provider=provider, base_url=base_url, status="authorized", expires_at__gt=timezone.now(),
    ).order_by("-created_at")
    if model:existing=existing.filter(model_name=model)
    for candidate in ([] if force_new else existing):
        if grant_state(candidate)=="authorized":return public(candidate)
    row, created = AgentCredentialAuthorization.objects.get_or_create(run=run, provider=provider, defaults={
        "owner_id": owner, "base_url":base_url, "model_name":model, "expires_at": timezone.now() + timedelta(minutes=10),
    })
    if row.base_url!=base_url or row.model_name!=model:
        raise exceptions.ValidationError('Request a new authorization invocation to change endpoint or model.')
    return public(row)


@sensitive_variables()
def internal_status(run_id, token, authorization_id, resolve=False):
    run = internal_run(run_id, token)
    row = AgentCredentialAuthorization.objects.filter(id=authorization_id, **scope(run)).select_related("run__agent").first()
    if not row or str(row.owner_id) != str(owner_id(run)):
        raise exceptions.NotFound()
    from .credential_preauthorization import enforce_resolution
    effective_expiry = enforce_resolution(run, row)
    result = public(row)
    if effective_expiry is not None:
        result["expires_at"] = effective_expiry.isoformat()
    if resolve:
        if result["status"] != "authorized" or run.status != AgentDisplayRun.STATUS_RUNNING:
            raise exceptions.PermissionDenied("Authorization is not active for this running invocation.")
        # This response goes only to the authenticated runtime callback, never MCP.
        result = {"secret": decrypt_secret((row.source_grant or row).encrypted_secret), "provider": row.provider,
                  "base_url": row.base_url, "model":row.model_name, "protocol":PROVIDERS[row.provider]["protocol"], "expires_at": result["expires_at"]}
    return result


def browser_row(request, authorization_id, lock=False):
    if getattr(request, "api_key", None) is not None or not request.user.is_authenticated or not request.user.is_active:
        raise exceptions.PermissionDenied("Sign in with the credential owner's personal account.")
    query = AgentCredentialAuthorization.objects.select_related("run__agent")
    if lock: query = query.select_for_update()
    row = query.filter(id=authorization_id, owner_id=request.user.pk).first()
    if not row or str(owner_id(row.run)) != str(request.user.pk):
        raise exceptions.NotFound()
    enabled(row.run)
    return row


def saved_grants(row):
    return AgentCredentialAuthorization.objects.filter(**scope(row.run),owner_id=row.owner_id,
        status='authorized',preauthorized=False,expires_at__gt=timezone.now(),source_grant__isnull=True,
        issued_key_id__isnull=True).exclude(encrypted_secret='')


def browser_details(row):
    from .credential_router import available
    return {**public(row),'providers':[{'id':key,**value} for key,value in PROVIDERS.items()],
            'nexus_routers':available(row), 'nexus_router_delegation':False,
            'saved_credentials':[{'authorization_id':str(item.id),'provider':item.provider,'base_url':item.base_url,
                'model':item.model_name,'expires_at':item.expires_at.isoformat()} for item in saved_grants(row)[:50]]}


@transaction.atomic
@sensitive_variables()
def decide(request, authorization_id):
    row = browser_row(request, authorization_id, lock=True)
    action = request.data.get("action")
    if action in ("decline", "revoke"):
        if row.issued_key_id:
            raise exceptions.PermissionDenied('Router delegation is not supported by this host.')
        row.status = "revoked" if row.status == "authorized" else "cancelled"
        row.encrypted_secret = ""
    elif action == "authorize":
        if row.status != "pending" or row.expires_at <= timezone.now():
            raise exceptions.ValidationError("Authorization is no longer pending. Start a new request.")
        requested_provider=request.data.get('provider',row.provider)
        provider,base_url,model = configuration(requested_provider,
            request.data.get('base_url',row.base_url if requested_provider==row.provider else None),
            request.data.get('model',row.model_name),require_model=True,resolve=True)
        if request.data.get("consent") is not True:
            raise exceptions.ValidationError("Explicit consent is required.")
        ttl = request.data.get("ttl_seconds", 3600)
        if type(ttl) is not int or not 300 <= ttl <= 86400:
            raise exceptions.ValidationError("Authorization lifetime must be 5 minutes to 24 hours.")
        source_id=request.data.get('saved_authorization_id')
        expires=timezone.now()+timedelta(seconds=ttl)
        router_id=request.data.get('router_id')
        if provider=='nexus' and router_id:
            if source_id or request.data.get('secret'):
                raise exceptions.ValidationError('Choose a Router without supplying another credential.')
            from .credential_router import issue
            issue(row,router_id,model)
        elif source_id:
            if request.data.get('secret'):
                raise exceptions.ValidationError('Choose a saved credential or enter a new key.')
            try:source=saved_grants(row).select_for_update().filter(pk=source_id,provider=provider,base_url=base_url).first()
            except (ValueError,DjangoValidationError,exceptions.ValidationError):source=None
            if source is None:
                raise exceptions.ValidationError('Saved credential is unavailable for this caller and endpoint.')
            row.source_grant=source
            row.encrypted_secret=''
            expires=min(expires,source.expires_at)
        else:
            secret=request.data.get('secret')
            if not isinstance(secret,str) or not 8<=len(secret)<=8192 or any(c.isspace() for c in secret):
                raise exceptions.ValidationError('Enter a valid provider API key or access token.')
            row.source_grant=None
            row.encrypted_secret=encrypt_secret(secret)
        if AgentCredentialAuthorization.objects.filter(run_id=row.run_id,provider=provider).exclude(pk=row.pk).exists():
            raise exceptions.ValidationError('This invocation already has a request for that provider. Start a new invocation.')
        row.provider,row.base_url,row.model_name=provider,base_url,model
        row.status="authorized"
        row.expires_at=expires
    else:
        raise exceptions.ValidationError("Unknown authorization action.")
    row.save(update_fields=["status", "encrypted_secret", "expires_at", "updated_at", "provider", "base_url", "model_name", "source_grant", "router_id", "issued_key_id"])
    return public(row)


def chunks(request, run, session, seen):
    from django.core.cache import cache
    from .credential_elicitation import options, form
    if run is None or session is None: return
    caps = session.client_capabilities_json or {}
    if not (caps.get('elicitation') or caps.get('elicitation_url')):return
    if (session.caller_subject_hash != request_subject(request).subject_hash or
            session.caller_subject_hash != run.caller_subject_hash): return
    for row in run.credential_authorizations.select_related('run__agent','run__consumer_tenant','owner').all():
        key = 'credential:' + str(row.id)
        request_id = 'nexus-credential-' + str(row.id)
        fallback = bool(cache.get(request_id+':configure'))
        seen_key = key+':url' if fallback else key
        status = public(row)['status']
        if status == 'pending' and seen_key not in seen:
            choices = options(row) if caps.get('elicitation') and not fallback else {}
            if choices:
                params = form(row, choices)
            elif caps.get('elicitation_url'):
                params = {'mode':'url','elicitationId':str(row.id),'url':public(row)['authorization_url'],
                    'message':'Configure access for '+run.agent.name+' on the secure page. New credentials are entered only on that page.'}
            else:continue
            cache.set(request_id, {'session_hash':session.external_session_hash,
                'subject_hash':session.caller_subject_hash,'agent_id':str(run.agent_id),
                'mode':params['mode'],'choices':choices}, timeout=600)
            seen.add(seen_key)
            yield ('event: message\ndata: '+json.dumps({'jsonrpc':'2.0','id':request_id,
                'method':'elicitation/create','params':params})+'\n\n').encode()
        elif status == 'authorized' and (key in seen or key+':url' in seen) and key+':done' not in seen:
            seen.add(key+':done')
            if caps.get('elicitation_url'):
                yield ('event: message\ndata: '+json.dumps({'jsonrpc':'2.0','method':'notifications/elicitation/complete',
                    'params':{'elicitationId':str(row.id)}})+'\n\n').encode()


@transaction.atomic
def handle_response(request, agent_id, payload, headers):
    from django.core.cache import cache
    from types import SimpleNamespace
    from .runtime_services import RuntimeMCPResult, _header_value
    rid = str(payload.get('id') or '')
    if not rid.startswith('nexus-credential-') or 'method' in payload:return None
    binding = cache.get(rid)
    response = RuntimeMCPResult(status_code=202,headers={},body=b'')
    if not isinstance(binding,dict):return response
    if (binding.get('agent_id')!=str(agent_id) or binding.get('subject_hash')!=request_subject(request).subject_hash
            or binding.get('session_hash')!=hash_token(_header_value(headers,'Mcp-Session-Id') or '')):return response
    row = AgentCredentialAuthorization.objects.select_for_update().select_related('run','owner').filter(
        id=rid.removeprefix('nexus-credential-')).first()
    if row is None or row.status!='pending' or row.expires_at<=timezone.now():
        cache.delete(rid);return response
    if str(owner_id(row.run))!=str(row.owner_id):
        cache.delete(rid);return response
    result=payload.get('result') if isinstance(payload.get('result'),dict) else {}
    action=result.get('action')
    if action in ('decline','cancel'):
        row.status='cancelled';row.encrypted_secret=''
        row.save(update_fields=['status','encrypted_secret','updated_at'])
    elif action=='accept' and binding.get('mode')=='form':
        content=result.get('content') if isinstance(result.get('content'),dict) else {}
        selection=content.get('selection')
        if selection=='Configure securely':
            cache.set(rid+':configure',True,timeout=600)
        else:
            choice=binding.get('choices',{}).get(selection) if isinstance(selection,str) else None
            if not choice or content.get('consent') is not True or content.get('lifetime') not in ('300','3600','86400'):
                raise exceptions.ValidationError('Choose an offered authorization and explicitly confirm it.')
            # Server-owned fields only. Never copy arbitrary result fields or secrets.
            decision={**choice,'action':'authorize','consent':True,'ttl_seconds':int(content['lifetime'])}
            decide(SimpleNamespace(user=row.owner,data=decision),str(row.pk))
    # URL accept acknowledges navigation only, never approval.
    cache.delete(rid)
    return response
