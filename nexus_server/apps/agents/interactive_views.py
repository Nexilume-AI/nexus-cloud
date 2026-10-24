"""Interactive UI capabilities: browser, runtime and invocation are distinct scopes."""
import asyncio
import hmac
import json
import re
import secrets
import time
import uuid
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import redis
import redis.asyncio as aioredis
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, StreamingHttpResponse
from django.utils import timezone
from jsonschema import Draft202012Validator, SchemaError, ValidationError
from rest_framework import exceptions
from rest_framework.response import Response

from .caller_identity import runtime_identity
from .models import Agent, AgentInteractiveView, AgentViewAction
from .view_security import digest, live as check_live
from .wave_views import NarrowView

COOKIE = '__Secure-nexus-view'
MAX_STATE = 131072
NAME = re.compile(r'[a-zA-Z][a-zA-Z0-9_]{0,63}\Z')


class ViewEndpoint(NarrowView):
    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.method == 'POST' and not isinstance(request.data, dict):
            raise exceptions.ValidationError('A JSON object is required.')


def bounded(value, limit):
    try:
        encoded = json.dumps(value, allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError):
        raise exceptions.ValidationError('Invalid JSON.') from None
    if len(encoded) > limit:
        raise exceptions.ValidationError('View payload exceeds its limit.')
    return value


def definition(value):
    bounded(value, 300000)
    if not isinstance(value, dict) or value.get('template', 'panel') not in ('panel', 'custom'):
        raise exceptions.ValidationError('Choose panel or custom.')
    actions = value.get('actions', {})
    if not isinstance(actions, dict) or len(actions) > 32:
        raise exceptions.ValidationError('At most 32 declared actions are supported.')
    for name, action in actions.items():
        if not NAME.fullmatch(name) or not isinstance(action, dict):
            raise exceptions.ValidationError('Invalid action declaration.')
        schema = action.get('input_schema', {'type': 'object', 'additionalProperties': False})
        bounded(schema, 8192)
        # No remote/local reference resolution, regex engines or recursive schemas.
        def safe(node, depth=0):
            if depth > 12:
                raise exceptions.ValidationError('Action schema is too deeply nested.')
            if isinstance(node, dict):
                if any(k in node for k in ('$ref', '$dynamicRef', 'pattern', 'patternProperties')):
                    raise exceptions.ValidationError('Schema references and regex patterns are not supported.')
                for child in node.values(): safe(child, depth + 1)
            elif isinstance(node, list):
                for child in node: safe(child, depth + 1)
        safe(schema)
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError:
            raise exceptions.ValidationError('Invalid action input schema.') from None
        if not isinstance(schema, dict) or schema.get('type') != 'object':
            raise exceptions.ValidationError('Action arguments must be an object.')
        actions[name] = {'label': str(action.get('label', name))[:120], 'input_schema': schema}
    html = value.get('html', '')
    if not isinstance(html, str) or len(html.encode()) > 262144:
        raise exceptions.ValidationError('Custom HTML must be at most 256 KiB.')
    if value.get('template') == 'custom' and not html.strip():
        raise exceptions.ValidationError('Custom HTML is required.')
    return {'title': str(value.get('title', 'Interactive View'))[:120],
            'template': value.get('template', 'panel'), 'actions': actions, 'html': html}


def path(view_id):
    return f'/api/v1/agent-interactive-views/{view_id}/'


def live(row):
    check_live(row)
    if row.run.caller_hidden_at:
        raise exceptions.NotFound('This invocation is no longer available.')
    return row


def get_row(view_id, lock=False):
    q = AgentInteractiveView.objects.select_related('run__agent')
    if lock: q = q.select_for_update(of=('self',))
    return live(q.filter(pk=view_id).first())


def browser(request, view_id, lock=False):
    row = get_row(view_id, lock)
    token = request.COOKIES.get(COOKIE, '')
    if not token or not row.browser_hash or not hmac.compare_digest(digest(token), row.browser_hash):
        raise exceptions.PermissionDenied('Open the complete fresh link returned by your Agent.')
    return row


def producer(request, view_id, lock=False):
    row = get_row(view_id, lock)
    token = request.headers.get('X-Nexus-View-Token', '')
    if not token or not hmac.compare_digest(digest(token), row.producer_hash):
        raise exceptions.PermissionDenied('Invalid view runtime capability.')
    return row


def csrf(request):
    if request.headers.get('X-Nexus-View') != '1' or request.content_type != 'application/json':
        raise exceptions.PermissionDenied()
    origin = request.headers.get('Origin', '')
    if origin and origin != str(settings.NEXUS_PUBLIC_BASE_URL).rstrip('/'):
        raise exceptions.PermissionDenied()


def notify(view_id):
    try:
        with redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2) as client:
            client.publish('nexus:view:' + str(view_id), 'update')
    except redis.RedisError:
        pass  # DB is authoritative; streams replay on reconnect.


def changed(row):
    row.revision += 1
    row.save()
    transaction.on_commit(lambda: notify(row.id))


def ticket(row):
    base = str(getattr(settings, 'NEXUS_PUBLIC_BASE_URL', '')).rstrip('/')
    u = urlsplit(base)
    if u.scheme != 'https' or not u.netloc or u.username or u.query or u.fragment:
        raise exceptions.APIException('A secure public URL must be configured.')
    token = secrets.token_urlsafe(32)
    row.ticket_hash = digest(token)
    row.ticket_expires_at = min(row.expires_at, timezone.now() + timedelta(minutes=10))
    row.save(update_fields=['ticket_hash', 'ticket_expires_at'])
    return {'view_id': str(row.id), 'url': base + path(row.id) + '#ticket=' + token,
            'expires_at': row.expires_at.isoformat(), 'link_expires_at': row.ticket_expires_at.isoformat(),
            'single_use_link': True}


def online(row):
    return bool(row.producer_seen_at and row.producer_seen_at > timezone.now() - timedelta(seconds=35))


def expire_actions(row):
    # A lost execution is NOT retried automatically: its side effects may have happened.
    count = row.actions.filter(status__in=['pending', 'running'], deadline__lte=timezone.now()).update(
        status='expired', result={'error': 'Action timed out. Check Agent state before trying again.'})
    if count:
        AgentInteractiveView.objects.filter(pk=row.id).update(revision=row.revision + 1)
        row.revision += 1
    return count


def snapshot(row):
    expire_actions(row)
    actions = list(row.actions.order_by('-created_at').values('request_id', 'name', 'status', 'result')[:32])
    for action in actions: action['request_id'] = str(action['request_id'])
    return {'view_id': str(row.id), 'definition': row.definition, 'state': row.state,
            'revision': row.revision, 'online': online(row), 'expires_at': row.expires_at.isoformat(), 'actions': actions}


class InternalView(ViewEndpoint):
    @transaction.atomic
    def post(self, request, run_id, view_id=None):
        from .runtime_services import get_internal_interaction_run
        run = get_internal_interaction_run(run_id=str(run_id), token=request.headers.get('X-Nexus-Interaction-Token', ''))
        if run.run_kind != 'invocation' or run.status != 'running' or run.caller_hidden_at or run.agent.status != Agent.STATUS_ACTIVE:
            raise exceptions.PermissionDenied('Active invocation required.')
        owner = runtime_identity(run)['owner_scope']
        Agent.objects.select_for_update().get(pk=run.agent_id)
        if view_id:
            row = AgentInteractiveView.objects.select_for_update().select_related('run__agent').filter(pk=view_id, owner_scope=owner).first()
            live(row)
            if request.data.get('action') == 'close':
                row.closed = True
                changed(row)
                return Response({'closed': True})
            if request.data.get('action', 'ticket') != 'ticket':
                raise exceptions.ValidationError('Unknown operation.')
            return Response(ticket(row))
        if AgentInteractiveView.objects.filter(owner_scope=owner, closed=False, expires_at__gt=timezone.now()).count() >= 8:
            raise exceptions.Throttled(detail='Close an existing view first (8 per caller and Agent).')
        try: ttl = int(request.data.get('ttl_seconds', 3600))
        except (ValueError, TypeError): raise exceptions.ValidationError('Invalid TTL.') from None
        if not 60 <= ttl <= 3600: raise exceptions.ValidationError('TTL must be 60–3600 seconds.')
        state = bounded(request.data.get('state', {}), MAX_STATE)
        if not isinstance(state, dict): raise exceptions.ValidationError('State must be an object.')
        token = secrets.token_urlsafe(32)
        row = AgentInteractiveView.objects.create(run=run, owner_scope=owner, definition=definition(request.data.get('definition')),
            state=state, producer_hash=digest(token), expires_at=timezone.now() + timedelta(seconds=ttl))
        info = ticket(row)
        # Runtime only. SDK returns public metadata separately, never this capability.
        info['_runtime'] = {'token': token, 'url': str(settings.NEXUS_PUBLIC_BASE_URL).rstrip('/') + path(row.id) + 'runtime/'}
        return Response(info)


class PageView(ViewEndpoint):
    def get(self, request, view_id):
        nonce = secrets.token_urlsafe(24)
        html = Path(__file__).with_name('interactive_page.html').read_text(encoding='utf-8').replace('__NONCE__', nonce)
        response = HttpResponse(html)
        response['X-Frame-Options'] = 'DENY'
        response['Content-Security-Policy'] = f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; frame-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        return response


class RedeemView(ViewEndpoint):
    @transaction.atomic
    def post(self, request, view_id):
        csrf(request)
        row = get_row(view_id, True)
        token = request.data.get('ticket')
        if not isinstance(token, str) or not token or not row.ticket_hash or row.ticket_expires_at <= timezone.now() or not hmac.compare_digest(digest(token), row.ticket_hash):
            raise exceptions.PermissionDenied('Link expired or already used. Ask your Agent for a fresh link.')
        secret = secrets.token_urlsafe(32)
        row.ticket_hash = ''
        row.browser_hash = digest(secret)
        changed(row)
        response = Response({'ready': True})
        response.set_cookie(COOKIE, secret, max_age=max(1, int((row.expires_at - timezone.now()).total_seconds())),
                            secure=True, httponly=True, samesite='Strict', path=path(row.id))
        return response


class StateView(ViewEndpoint):
    @transaction.atomic
    def get(self, request, view_id):
        return Response(snapshot(browser(request, view_id, True)))


class CustomView(ViewEndpoint):
    def get(self, request, view_id):
        row = browser(request, view_id)
        if row.definition['template'] != 'custom': raise exceptions.NotFound()
        bridge = Path(__file__).with_name('interactive_bridge.js').read_text(encoding='utf-8')
        response = HttpResponse('<!doctype html><meta charset="utf-8"><script>' + bridge + '</script>' + row.definition['html'])
        # Header sandbox also applies if someone opens this URL as a top-level page.
        response['Content-Security-Policy'] = "sandbox allow-scripts allow-forms; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'; frame-ancestors 'self'"
        response['X-Frame-Options'] = 'SAMEORIGIN'
        return response


class ActionView(ViewEndpoint):
    @transaction.atomic
    def post(self, request, view_id):
        csrf(request)
        row = browser(request, view_id, True)
        expire_actions(row)
        try: rid = uuid.UUID(str(request.data.get('request_id')))
        except ValueError: raise exceptions.ValidationError('A UUID request_id is required.') from None
        name = request.data.get('name')
        args = bounded(request.data.get('arguments', {}), 32768)
        if not isinstance(name, str) or name not in row.definition['actions']:
            raise exceptions.ValidationError('Action is not declared by this Agent.')
        try: Draft202012Validator(row.definition['actions'][name]['input_schema']).validate(args)
        except ValidationError: raise exceptions.ValidationError('Arguments do not match the declared schema.') from None
        previous = row.actions.filter(request_id=rid).first()
        if previous:
            if previous.name != name or previous.arguments != args:
                raise exceptions.ValidationError('request_id was already used for different arguments.')
            return Response({'request_id': str(rid), 'status': previous.status})
        if not online(row): raise exceptions.APIException('Agent is disconnected. Reopen the view through MCP.')
        if row.actions.count() >= 256 or row.actions.filter(status__in=['pending', 'running']).count() >= 16:
            raise exceptions.Throttled(detail='View action queue is full.')
        row.actions.create(request_id=rid, name=name, arguments=args, deadline=timezone.now() + timedelta(seconds=120))
        changed(row)
        return Response({'request_id': str(rid), 'status': 'pending'}, status=202)


class RuntimeView(ViewEndpoint):
    def get(self, request, view_id):
        row = producer(request, view_id)
        return Response({'state': row.state, 'revision': row.revision})

    @transaction.atomic
    def post(self, request, view_id):
        row = producer(request, view_id, True)
        op = request.data.get('operation')
        row.producer_seen_at = timezone.now()
        if op == 'update':
            state = bounded(request.data.get('state'), MAX_STATE)
            if not isinstance(state, dict): raise exceptions.ValidationError('State must be an object.')
            # Optimistic concurrency protects independent worker updates.
            if request.data.get('revision') != row.revision:
                return Response({'error': 'revision_conflict', 'revision': row.revision}, status=409)
            row.state = state
        elif op == 'cancel_task':
            # The producer capability is bound to exactly this view and Run.
            # Never accept a caller-supplied task/run identifier here.
            if set(request.data) != {'operation'}:
                raise exceptions.ValidationError('Cancellation accepts no target identifier.')
            from .models import AgentExecutionTask
            from .task_execution import request_cancel
            task = AgentExecutionTask.objects.filter(run_id=row.run_id, execution__isnull=False).first()
            if task is None:
                raise exceptions.ValidationError('This view has no durable task.')
            request_cancel(task)
            task.refresh_from_db(fields=['status'])
            changed(row)
            return Response({'status': task.status})
        elif op == 'close': row.closed = True
        elif op in ('claim', 'complete'):
            expire_actions(row)
            try: rid = uuid.UUID(str(request.data.get('request_id')))
            except ValueError: raise exceptions.ValidationError('Invalid request_id.') from None
            action = row.actions.filter(request_id=rid).first()
            if not action: raise exceptions.NotFound()
            lease = request.data.get('lease')
            if not isinstance(lease, str) or not 32 <= len(lease) <= 128:
                raise exceptions.ValidationError('Invalid execution lease.')
            if op == 'claim':
                if action.status != 'pending': return Response({'claimed': False, 'status': action.status})
                action.status = 'running'
                action.lease_hash = digest(lease)
                action.deadline = timezone.now() + timedelta(seconds=60)
            else:
                if not hmac.compare_digest(action.lease_hash, digest(lease)):
                    raise exceptions.PermissionDenied('Execution lease does not match.')
                if action.status in ('succeeded', 'failed'): return Response({'status': action.status})
                if action.status != 'running': raise exceptions.ValidationError('Action is no longer running.')
                action.result = bounded(request.data.get('result', {}), 32768)
                action.status = 'failed' if request.data.get('failed') is True else 'succeeded'
            action.save()
            changed(row)
            return Response({'claimed': op == 'claim', 'status': action.status, 'revision': row.revision})
        elif op != 'heartbeat': raise exceptions.ValidationError('Unknown runtime operation.')
        changed(row)
        return Response({'revision': row.revision, 'state': row.state})


async def stream(view_id, capability_hash, runtime=False):
    client = aioredis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=3)
    pubsub = client.pubsub()
    until = time.monotonic() + 55
    last = None
    try:
        await pubsub.subscribe('nexus:view:' + str(view_id))
        while time.monotonic() < until:
            ack = await pubsub.get_message(timeout=3)
            if ack and ack['type'] == 'subscribe': break
        while time.monotonic() < until:
            def read():
                # Lock expiry/revision mutations against browser submits/runtime updates.
                with transaction.atomic():
                    row = get_row(view_id, True)
                    expected = row.producer_hash if runtime else row.browser_hash
                    if not expected or not hmac.compare_digest(expected, capability_hash): raise exceptions.NotFound()
                    expire_actions(row)
                    if runtime:
                        AgentInteractiveView.objects.filter(pk=row.id).update(producer_seen_at=timezone.now())
                        return {'actions': [{'request_id': str(a.request_id), 'name': a.name, 'arguments': a.arguments}
                                            for a in row.actions.filter(status='pending').order_by('created_at')]}
                    value = snapshot(row)
                    value.pop('definition', None)
                    return value
            try: value = await sync_to_async(read)()
            except exceptions.NotFound:
                yield 'event: closed\ndata: {}\n\n'
                return
            encoded = json.dumps(value, separators=(',', ':'))
            if encoded != last:
                yield 'event: ' + ('actions' if runtime else 'state') + '\ndata: ' + encoded + '\n\n'
                last = encoded
            else: yield ': heartbeat\n\n'
            await pubsub.get_message(ignore_subscribe_messages=True, timeout=10)
    except (redis.RedisError, asyncio.TimeoutError):
        yield 'retry: 3000\nevent: reconnect\ndata: {}\n\n'
    finally:
        await pubsub.aclose()
        await client.aclose()


class EventsView(ViewEndpoint):
    runtime = False

    def get(self, request, view_id):
        row = producer(request, view_id) if self.runtime else browser(request, view_id)
        response = StreamingHttpResponse(stream(row.id, row.producer_hash if self.runtime else row.browser_hash, self.runtime), content_type='text/event-stream')
        response['X-Accel-Buffering'] = 'no'
        return response
