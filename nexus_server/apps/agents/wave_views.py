"""Narrow browser capabilities for immutable waveform files; no account session delegation."""
import asyncio
import hashlib
import hmac
import json
import mimetypes
import re
import secrets
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import redis
import redis.asyncio as aioredis
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.negotiation import DefaultContentNegotiation
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .caller_identity import runtime_identity
from .models import Agent, AgentFileTransfer, AgentWaveView

COOKIE = '__Secure-nexus-wave'
TICKET_SECONDS = 600
VIEW_SECONDS = 3600
COMMANDS = re.compile(r'(?:variable_add [\w.$\[\]:/\\-]{1,512}|divider_add "[\w -]{1,80}"|item_set_color [\w#]{1,32}|item_set_format (?:Hexadecimal|Bits|Unsigned|Signed|ASCII)|zoom_fit|zoom_to -?\d{1,30} -?\d{1,30}|(?:cursor_set|goto_time) -?\d{1,30}|marker_set_at -?\d{1,30} \d{1,3})\Z')


def digest(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def base_path(view_id):
    return f'/api/v1/agent-wave-views/{view_id}/'


def harden(response):
    response['Cache-Control'] = 'private, no-store'
    response['Referrer-Policy'] = 'no-referrer'
    response['X-Content-Type-Options'] = 'nosniff'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response


def live(row):
    if not row or row.closed or row.expires_at <= timezone.now() or row.run.agent.status != Agent.STATUS_ACTIVE:
        raise exceptions.NotFound('Wave view is closed or expired. Open a new view through MCP.')
    # Personal credentials authenticate as their verified owner, never Enterprise Keys.
    from django.contrib.auth import get_user_model
    from nexus_personal.models import PersonalInvocationUsage
    if row.run.caller_principal_type != 'user' or not get_user_model().objects.filter(pk=row.run.caller_principal_id, is_active=True).exists():
        raise exceptions.NotFound('The viewer owner is no longer active.')
    credential_ids = PersonalInvocationUsage.objects.filter(run_id=row.run_id).exclude(agent_credential_id=None).values_list('agent_credential_id', flat=True)
    from nexus_personal.agent_credentials import _current
    from types import SimpleNamespace
    for credential_id in set(credential_ids):
        try:
            credential, user = _current(SimpleNamespace(META={}, headers={}), credential_id)
        except exceptions.APIException:
            raise exceptions.NotFound('The viewer credential is no longer active.') from None
        if str(user.pk) != row.run.caller_principal_id or str(row.run.agent_id) not in credential.agent_ids:
            raise exceptions.NotFound('The viewer credential no longer permits this Agent.')
    return row


def browser_view(request, view_id):
    row = live(AgentWaveView.objects.select_related('run__agent').filter(pk=view_id).first())
    secret = request.COOKIES.get(COOKIE, '')
    if not secret or not row.browser_hash or not hmac.compare_digest(digest(secret), row.browser_hash):
        raise exceptions.PermissionDenied('Open the complete, fresh viewer link returned by MCP.')
    return row


def validate_snapshot(value, count):
    if not isinstance(value, dict) or len(json.dumps(value)) > 32768:
        raise exceptions.ValidationError('Invalid view state.')
    panels = value.get('panels', [])
    if not isinstance(panels, list) or len(panels) != count:
        raise exceptions.ValidationError('One panel per waveform is required.')
    clean = []
    for panel in panels:
        command = panel.get('commands', '')
        if not isinstance(command, str) or len(command) > 12000 or any(not COMMANDS.fullmatch(c) for c in command.split(';') if c):
            raise exceptions.ValidationError('Unsupported viewer command or signal name.')
        clean.append({'label': str(panel.get('label', 'Waveform'))[:120], 'commands': command})
    # Never retain internal paths, local viewer tokens, HTML, or executable commands.
    return {'panels': clean, 'annotation': str(value.get('annotation', ''))[:4000]}


def notify(view_id):
    try:
        with redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2) as client:
            client.publish('nexus:wave:' + str(view_id), 'update')
    except redis.RedisError:
        pass  # Persistent state is replayed on EventSource reconnect.


def public_state(row):
    return {'view_id': str(row.id), 'revision': row.revision, 'expires_at': row.expires_at.isoformat(),
            'snapshot': row.snapshot, 'files': [base_path(row.id) + f'files/{i}/' for i in range(len(row.file_ids))]}


def issue_ticket(row):
    base = str(getattr(settings, 'NEXUS_PUBLIC_BASE_URL', '')).rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.query or parsed.fragment:
        raise exceptions.APIException('Secure public viewer URL is not configured.')
    ticket = secrets.token_urlsafe(32)
    row.ticket_hash = digest(ticket)
    row.ticket_expires_at = min(row.expires_at, timezone.now() + timedelta(seconds=TICKET_SECONDS))
    row.save(update_fields=['ticket_hash', 'ticket_expires_at'])
    return {'public_view_id': str(row.id), 'url': base + base_path(row.id) + '#ticket=' + ticket,
            'link_expires_at': row.ticket_expires_at.isoformat(), 'expires_at': row.expires_at.isoformat(),
            'single_use_link': True, 'browser_state': row.browser_state}


class Negotiation(DefaultContentNegotiation):
    def select_renderer(self, request, renderers, format_suffix=None):
        return renderers[0], renderers[0].media_type


class NarrowView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    content_negotiation_class = Negotiation

    def finalize_response(self, request, response, *args, **kwargs):
        return harden(super().finalize_response(request, response, *args, **kwargs))


class InternalWaveView(NarrowView):
    @transaction.atomic
    def post(self, request, run_id, view_id=None):
        from .runtime_services import get_internal_interaction_run
        run = get_internal_interaction_run(run_id=str(run_id), token=request.headers.get('X-Nexus-Interaction-Token', ''))
        if run.run_kind != 'invocation' or run.status != 'running' or run.caller_hidden_at or run.agent.status != Agent.STATUS_ACTIVE:
            raise exceptions.PermissionDenied('Active invocation required.')
        owner = runtime_identity(run)['owner_scope']
        # Serialize creation and updates without trusting worker-supplied ownership.
        Agent.objects.select_for_update().get(pk=run.agent_id)
        if view_id:
            row = AgentWaveView.objects.select_for_update().select_related('run__agent').filter(pk=view_id, owner_scope=owner).first()
            if not row:
                raise exceptions.NotFound('Wave view not found.')
            action = request.data.get('action', 'ticket')
            if action == 'close':
                row.closed = True
                row.browser_hash = row.ticket_hash = ''
                row.save(update_fields=['closed', 'browser_hash', 'ticket_hash'])
                transaction.on_commit(lambda: notify(row.id))
                return Response({'closed': True})
            live(row)
            if action == 'update':
                row.snapshot = validate_snapshot(request.data.get('snapshot'), len(row.file_ids))
                row.revision += 1
                row.save(update_fields=['snapshot', 'revision'])
                transaction.on_commit(lambda: notify(row.id))
                return Response({'revision': row.revision, 'browser_state': row.browser_state})
            if action != 'ticket':
                raise exceptions.ValidationError('Unknown action.')
        else:
            if AgentWaveView.objects.filter(owner_scope=owner, closed=False, expires_at__gt=timezone.now()).count() >= 8:
                raise exceptions.Throttled(detail='Close an existing view before opening another.')
            ids = request.data.get('file_ids', [])
            if not isinstance(ids, list) or not 1 <= len(ids) <= 2 or len(set(map(str, ids))) != len(ids):
                raise exceptions.ValidationError('Select one or two uploaded FST files.')
            try:
                files = list(AgentFileTransfer.objects.filter(id__in=ids, run=run, direction='output', state='ready', expires_at__gt=timezone.now()))
            except (ValueError, TypeError):
                raise exceptions.ValidationError('Invalid file IDs.') from None
            if len(files) != len(ids) or any(not f.name.lower().endswith('.fst') or f.size_bytes > 268435456 for f in files):
                raise exceptions.ValidationError('Ready FST outputs from this invocation are required (256 MiB per file).')
            row = AgentWaveView.objects.create(run=run, owner_scope=owner, file_ids=list(map(str, ids)),
                snapshot=validate_snapshot(request.data.get('snapshot'), len(ids)),
                expires_at=min(timezone.now() + timedelta(seconds=VIEW_SECONDS), *(f.expires_at for f in files)))
        return Response(issue_ticket(row))


class PageView(NarrowView):
    def get(self, request, view_id):
        # Generic shell contains no file names or account metadata before ticket redemption.
        nonce = secrets.token_urlsafe(24)
        html = Path(__file__).with_name('wave_page.html').read_text(encoding='utf-8').replace('__NONCE__', nonce)
        response = HttpResponse(html)
        response['X-Frame-Options'] = 'DENY'
        response['Content-Security-Policy'] = f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; frame-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        return response


class RedeemView(NarrowView):
    @transaction.atomic
    def post(self, request, view_id):
        row = live(AgentWaveView.objects.select_for_update().select_related('run__agent').filter(pk=view_id).first())
        token = request.data.get('ticket', '')
        if not isinstance(token, str) or not token or not row.ticket_hash or not row.ticket_expires_at or row.ticket_expires_at <= timezone.now() or not hmac.compare_digest(digest(token), row.ticket_hash):
            raise exceptions.PermissionDenied('Viewer link expired or already used. Request a fresh link through get_view_state.')
        secret = secrets.token_urlsafe(32)
        row.ticket_hash = ''
        row.browser_hash = digest(secret)
        row.save(update_fields=['ticket_hash', 'browser_hash'])
        response = Response({'ready': True})
        response.set_cookie(COOKIE, secret, max_age=max(1, int((row.expires_at - timezone.now()).total_seconds())),
                            secure=True, httponly=True, samesite='Strict', path=base_path(row.id))
        return response


class StateView(NarrowView):
    def get(self, request, view_id):
        return Response(public_state(browser_view(request, view_id)))

    def post(self, request, view_id):
        # Browser reports readiness only; desired commands remain MCP-controlled.
        if request.headers.get('X-Nexus-Wave') != '1' or request.content_type != 'application/json':
            raise exceptions.PermissionDenied()
        origin = request.headers.get('Origin', '')
        if origin and origin != str(settings.NEXUS_PUBLIC_BASE_URL).rstrip('/'):
            raise exceptions.PermissionDenied()
        row = browser_view(request, view_id)
        status = request.data.get('status')
        if status not in ('loading', 'ready', 'error'):
            raise exceptions.ValidationError('Invalid browser status.')
        AgentWaveView.objects.filter(pk=row.id).update(browser_state={'status': status, 'revision': row.revision, 'at': timezone.now().isoformat()})
        return Response({'ok': True})


async def events(view_id, browser_hash):
    client = aioredis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=3)
    pubsub = client.pubsub()
    deadline = time.monotonic() + 55
    try:
        await pubsub.subscribe('nexus:wave:' + str(view_id))
        while time.monotonic() < deadline:
            message = await pubsub.get_message(timeout=3)
            if message and message['type'] == 'subscribe':
                break
        dirty = True
        while time.monotonic() < deadline:
            # Validate on heartbeat too: revocation/expiry never depends on Redis delivery.
            try:
                row = await sync_to_async(lambda: live(AgentWaveView.objects.select_related('run__agent').filter(pk=view_id).first()))()
            except exceptions.NotFound:
                yield 'event: closed\ndata: {}\n\n'
                return
            if not row or row.browser_hash != browser_hash or row.closed or row.expires_at <= timezone.now() or row.run.agent.status != Agent.STATUS_ACTIVE:
                yield 'event: closed\ndata: {}\n\n'
                return
            if dirty:
                yield 'id: ' + str(row.revision) + '\nevent: state\ndata: ' + json.dumps(public_state(row)) + '\n\n'
                dirty = False
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=10)
            if message:
                dirty = True
            else:
                yield ': heartbeat\n\n'
    except (redis.RedisError, asyncio.TimeoutError):
        yield 'retry: 5000\nevent: reconnect\ndata: {}\n\n'
    finally:
        await pubsub.aclose()
        await client.aclose()


class EventsView(NarrowView):
    def get(self, request, view_id):
        row = browser_view(request, view_id)
        response = StreamingHttpResponse(events(row.id, row.browser_hash), content_type='text/event-stream')
        response['X-Accel-Buffering'] = 'no'
        return response


class WaveFileView(NarrowView):
    def get(self, request, view_id, index):
        from .file_views import transfer_download
        row = browser_view(request, view_id)
        if not 0 <= index < len(row.file_ids):
            raise exceptions.NotFound()
        file = AgentFileTransfer.objects.filter(pk=row.file_ids[index], run=row.run, expires_at__gt=timezone.now()).first()
        if not file:
            raise exceptions.NotFound()
        return transfer_download(request, file)


class AssetView(NarrowView):
    def get(self, request, view_id, name):
        browser_view(request, view_id)
        if name not in ('surfer.js', 'surfer_bg.wasm'):
            raise exceptions.NotFound()
        root = Path(getattr(settings, 'NEXUS_WAVE_ASSET_ROOT', '/opt/nexus-wave-viewer'))
        asset = root / name
        if asset.is_symlink() or not asset.is_file():
            raise exceptions.APIException('Wave viewer assets are not installed.')
        return FileResponse(asset.open('rb'), content_type=mimetypes.guess_type(name)[0] or 'application/octet-stream')


class CanvasView(NarrowView):
    def get(self, request, view_id):
        browser_view(request, view_id)
        nonce = secrets.token_urlsafe(24)
        html = Path(__file__).with_name('wave_canvas.html').read_text(encoding='utf-8').replace('__NONCE__', nonce)
        response = HttpResponse(html)
        response['X-Frame-Options'] = 'SAMEORIGIN'
        response['Content-Security-Policy'] = f"default-src 'none'; script-src 'self' 'wasm-unsafe-eval' 'nonce-{nonce}'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data: blob:; font-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
        return response
