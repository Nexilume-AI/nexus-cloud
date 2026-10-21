"""Explicit per-invocation upstream credentials supplied by an MCP client.

Only encrypted configuration crosses the HTTP boundary into a Run. These
headers must never be forwarded to an Agent, a Relay, or an execution queue.
"""
from datetime import timedelta
import hmac
import json
import re

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from rest_framework import exceptions
from apps.common.crypto import encrypt_secret, decrypt_secret
from .credential_config import configuration

PREFIX = 'HTTP_X_NEXUS_UPSTREAM_'
ATTRIBUTE = '_nexus_upstream_encrypted'
ROUTE = re.compile(r'^/api/v1/agents/[0-9a-fA-F-]{36}/(?:mcp(?:/(?:sse|messages))?|sse|messages)/?$')
FIELDS = {'PREAUTHORIZE', 'PROVIDER', 'ENDPOINT', 'MODEL', 'KEY', 'TTL_SECONDS'}


@sensitive_variables()
def capture(request):
    """Remove raw headers even on invalid paths, failed auth, or bad input."""
    raw = {k[len(PREFIX):]: request.META.pop(k) for k in list(request.META) if k.startswith(PREFIX)}
    if not raw:
        return
    request.__dict__.pop('headers', None)
    if hasattr(request, 'scope'):
        request.scope['headers'] = [(k, v) for k, v in request.scope.get('headers', [])
                                    if not k.lower().startswith(b'x-nexus-upstream-')]
    if not ROUTE.fullmatch(request.path_info):
        raise exceptions.ValidationError('Upstream preauthorization is supported only on Agent MCP endpoints.')
    if set(raw) - FIELDS:
        raise exceptions.ValidationError('Unknown upstream preauthorization header.')
    if raw == {'PREAUTHORIZE': 'false'}:
        return
    if raw.get('PREAUTHORIZE') != 'true':
        raise exceptions.ValidationError('Set X-Nexus-Upstream-Preauthorize to true to explicitly authorize model usage, or remove all upstream headers to use web authorization.')
    if not all(raw.get(k) for k in ('ENDPOINT', 'MODEL', 'KEY')):
        raise exceptions.ValidationError('Upstream preauthorization requires Endpoint, Model, and Key together.')
    secret = raw['KEY']
    if not isinstance(secret, str) or not 8 <= len(secret) <= 8192 or any(ord(c) < 33 or ord(c) > 126 for c in secret):
        raise exceptions.ValidationError('Invalid upstream API key.')
    provider, endpoint, model = configuration(raw.get('PROVIDER', 'custom_openai'), raw['ENDPOINT'], raw['MODEL'], require_model=True)
    ttl = raw.get('TTL_SECONDS', '3600')
    if not isinstance(ttl, str) or not re.fullmatch(r'[0-9]{3,5}', ttl) or not 300 <= int(ttl) <= 86400:
        raise exceptions.ValidationError('Upstream authorization lifetime must be 300 to 86400 seconds.')
    setattr(request, ATTRIBUTE, encrypt_secret(json.dumps({'provider': provider, 'base_url': endpoint,
            'model': model, 'secret': secret, 'ttl_seconds': int(ttl)})))


class UpstreamPreauthorizationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    @sensitive_variables()
    def __call__(self, request):
        try:
            capture(request)
        except exceptions.ValidationError as exc:
            response = JsonResponse({'error': {'code': 'INVALID_UPSTREAM_PREAUTHORIZATION', 'message': ' '.join(map(str, exc.detail))}}, status=400)
            response['Cache-Control'] = 'no-store, private'
            return response
        return self.get_response(request)


@sensitive_variables()
def attach(request, run):
    """Called inside Run creation's transaction after caller/Agent admission."""
    value = getattr(request, ATTRIBUTE, '')
    if not value:
        return
    from . import credential_services as credentials
    from .models import AgentCredentialAuthorization
    credentials.enabled(run)
    owner = credentials.owner_id(run)
    if not owner:
        raise exceptions.PermissionDenied('Use a personal Agent MCP credential for upstream preauthorization.')
    data = json.loads(decrypt_secret(value))
    # DNS/private-address validation is deferred until authenticated admission.
    provider, endpoint, model = configuration(data['provider'], data['base_url'], data['model'], require_model=True, resolve=True)
    AgentCredentialAuthorization.objects.create(run=run, owner_id=owner, provider=provider,
        base_url=endpoint, model_name=model, encrypted_secret=encrypt_secret(data['secret']),
        preauthorized=True, status='authorized', expires_at=timezone.now()+timedelta(seconds=data['ttl_seconds']))


def current(run):
    return run.credential_authorizations.filter(preauthorized=True).first()


@sensitive_variables()
def enforce_resolution(run, row):
    """Old config IDs work only when the current invocation carries the same grant scope/key."""
    configured = current(run)
    if not configured and not row.preauthorized:
        return
    if not configured or not row.preauthorized:
        raise exceptions.PermissionDenied('Authorization does not match this invocation configuration.')
    from .credential_services import grant_state
    if grant_state(configured) != 'authorized':
        raise exceptions.PermissionDenied('Invocation preauthorization is no longer active.')
    if (configured.provider, configured.base_url, configured.model_name) != (row.provider, row.base_url, row.model_name):
        raise exceptions.PermissionDenied('Model configuration changed; request a new authorization reference.')
    if not hmac.compare_digest(decrypt_secret(configured.encrypted_secret), decrypt_secret(row.encrypted_secret)):
        raise exceptions.PermissionDenied('Model configuration changed; request a new authorization reference.')

    return min(configured.expires_at, row.expires_at)
