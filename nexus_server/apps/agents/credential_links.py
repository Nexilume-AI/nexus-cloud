"""Single-decision authorization links, issued only at the authenticated MCP edge.

The pending grant is the durable consumption record. Opening a page or correcting
invalid input does not consume it; approve/cancel transitions are row-locked.
Never include these bearer links in runtime callbacks, job events or export data.
"""
import re
import uuid
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from rest_framework import exceptions

from apps.common.subjects import request_subject
from .models import AgentCredentialAuthorization
from . import credential_services as credentials

SALT = 'nexus.agent-authorization.single-decision.v1'
HEADER = 'X-Nexus-Authorization-Link'
LIFETIME = timedelta(minutes=5)


def deadline(row):
    return min((row.requested_at or row.created_at) + LIFETIME, row.expires_at)


def binding(row):
    run = row.run
    result = {'authorization': str(row.pk), 'owner': str(row.owner_id),
            'run': str(run.pk), 'agent': str(run.agent_id),
            'tenant': str(run.consumer_tenant_id), 'project': str(run.consumer_project_id or ''),
            'subject': run.caller_subject_hash, 'expires': int(deadline(row).timestamp())}
    if row.requested_at is not None:
        result['requested_at'] = row.requested_at.isoformat()
    return result


def usable(row):
    return (row.status == 'pending' and deadline(row) > timezone.now()
            and row.owner.is_active and str(credentials.owner_id(row.run)) == str(row.owner_id))


def decorate_response(request, agent_id, body):
    """Only caller output is decorated, after runtime/event recording has finished."""
    base = str(settings.NEXUS_PUBLIC_BASE_URL).rstrip('/')
    if not base.startswith('https://'):
        return body
    pattern = re.compile(re.escape(base.encode()) + rb'/api/v1/agent-authorizations/([0-9a-f-]{36})/page/(?![#?])')
    subject = None

    def replace(match):
        nonlocal subject
        try:
            authorization_id = uuid.UUID(match[1].decode())
        except ValueError:
            return match[0]
        if subject is None:
            subject = request_subject(request)
        from .caller_identity import principal_filter
        identity = principal_filter(subject.principal_type, subject.principal_id)
        if subject.principal_type not in ('api_key', 'api_key_external'):
            identity['run__caller_subject_hash'] = subject.subject_hash
        row = AgentCredentialAuthorization.objects.select_related('run', 'owner').filter(
            pk=authorization_id, run__agent_id=agent_id,
            **identity,
            run__consumer_tenant_id=getattr(request, 'tenant_id', None),
            run__consumer_project_id=getattr(request, 'project_id', None) or None,
        ).first()
        if row is None or not usable(row):
            return match[0]
        try:
            credentials.enabled(row.run)
        except exceptions.PermissionDenied:
            return match[0]
        token = signing.Signer(salt=SALT).sign_object(binding(row))
        return match[0] + b'#authorize=' + token.encode()

    return pattern.sub(replace, body)


def decorate_stream(request, agent_id, chunks):
    """Handle split SSE frames; bound buffering and preserve non-SSE responses."""
    pending = b''
    for chunk in chunks:
        pending += chunk
        while True:
            boundary = re.search(rb'\r?\n\r?\n', pending)
            if boundary is None:
                break
            frame, pending = pending[:boundary.end()], pending[boundary.end():]
            yield decorate_response(request, agent_id, frame)
        if len(pending) > 1024 * 1024:
            # Oversized frames retain the existing signed-in authorization URL.
            yield pending
            pending = b''
    if pending:
        yield decorate_response(request, agent_id, pending)


@sensitive_variables('token', 'payload')
def link_row(request, authorization_id, *, lock=False):
    token = request.headers.get(HEADER, '')
    if not isinstance(token, str) or not token or len(token) > 4096:
        raise exceptions.PermissionDenied('Authorization link is invalid, expired or already used.')
    try:
        payload = signing.Signer(salt=SALT).unsign_object(token)
    except (signing.BadSignature, ValueError, TypeError):
        raise exceptions.PermissionDenied('Authorization link is invalid, expired or already used.') from None
    query = AgentCredentialAuthorization.objects.select_related('run', 'owner')
    if lock:
        query = query.select_for_update()
    row = query.filter(pk=authorization_id).first()
    if row is None or payload != binding(row) or not usable(row):
        raise exceptions.PermissionDenied('Authorization link is invalid, expired or already used.')
    credentials.enabled(row.run)
    return row


def browser_details(row):
    """A bearer link permits supplying a new key, never browsing owner resources."""
    public = credentials.public(row)
    allowed = ('authorization_id', 'status', 'agent_name', 'agent_id', 'expires_at',
               'provider', 'provider_name', 'base_url', 'model', 'protocol', 'credential_supplied')
    result = {key: public[key] for key in allowed}
    if row.provider == 'nexus':
        result.update(provider='custom_openai', provider_name='Custom OpenAI-compatible',
                      base_url='', model='', protocol='openai')
    return {**result, **({'link_expires_at': deadline(row).isoformat()} if row.status == 'pending' else {}),
            'providers': [{'id': key, **value} for key, value in credentials.PROVIDERS.items()
                          if key != 'nexus'],
            'nexus_routers': [], 'saved_credentials': [], 'own_key_only': True}


@transaction.atomic
def decide(request, authorization_id):
    # A custom bearer header is required. Cookie authentication is deliberately
    # unused; also reject explicit cross-origin browser requests.
    origin = request.headers.get('Origin')
    public = urlsplit(settings.NEXUS_PUBLIC_BASE_URL)
    if origin and origin != f'{public.scheme}://{public.netloc}':
        raise exceptions.PermissionDenied('Cross-origin authorization is not allowed.')
    row = link_row(request, authorization_id, lock=True)
    if request.data.get('action') not in ('authorize', 'decline'):
        raise exceptions.ValidationError('This link only permits one approval or cancellation.')
    if request.data.get('action') == 'authorize':
        allowed = {'action', 'provider', 'base_url', 'model', 'secret', 'consent', 'ttl_seconds'}
        if set(request.data) - allowed or request.data.get('provider', row.provider) == 'nexus':
            raise exceptions.PermissionDenied('This link only accepts a new user-supplied API key. Sign in separately to access Nexus account resources.')
        secret = request.data.get('secret')
        if not isinstance(secret, str) or not secret:
            raise exceptions.ValidationError('Enter your own provider API key.')
    # Reuse validation/encryption only after denying all account-resource paths.
    # The link cannot list/reuse saved keys or mint keys against an owner Router.
    credentials.decide(SimpleNamespace(user=row.owner, data=request.data), str(row.pk))
    row.refresh_from_db()
    return browser_details(row)
