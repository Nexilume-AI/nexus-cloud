"""Short-lived, caller-bound file capabilities. Bytes stay on the Agent host.

Outbound workers exchange bounded chunks through Redis. No arbitrary upstream
URLs, host paths, account sessions, or model credentials are delegated.
"""
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from types import SimpleNamespace

import redis
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.handlers.asgi import ASGIRequest
from django.http import HttpResponse, StreamingHttpResponse
from django.utils.http import content_disposition_header
from rest_framework import exceptions
from rest_framework.response import Response

from apps.datasets.downloads import selected_range
from .caller_identity import runtime_identity
from .models import AgentDisplayRun
from .view_security import live, digest
from .wave_views import NarrowView

CHUNK = 4 * 1024 * 1024
TTL = 3600
COOKIE = '__Secure-nexus-file-bridge'
PREFIX = 'nexus:file-bridge:'


def db():
    return redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=3, socket_timeout=35)


def key(bridge_id, suffix=''):
    return PREFIX + str(bridge_id) + suffix


def base(bridge_id):
    return '/api/v1/agent-file-bridges/' + str(bridge_id) + '/'


def load(bridge_id, client=None):
    client = client or db()
    raw = client.get(key(bridge_id))
    if not raw:
        raise exceptions.NotFound('File link expired or revoked. Request a new link through MCP.')
    row = json.loads(raw)
    run = AgentDisplayRun.objects.select_related('agent').filter(pk=row['run']).first()
    if not run:
        raise exceptions.NotFound()
    live(SimpleNamespace(run=run, closed=False, expires_at=datetime.fromtimestamp(row['expires'], dt_timezone.utc)))
    return row


def authorized(request, bridge_id, *, worker=False):
    client = db()
    row = load(bridge_id, client)
    secret = request.headers.get('X-Nexus-File-Worker', '') if worker else request.COOKIES.get(COOKIE, '')
    expected = row['worker_hash'] if worker else client.get(key(bridge_id, ':browser'))
    if isinstance(expected, bytes): expected = expected.decode()
    if not secret or not expected or not hmac.compare_digest(digest(secret), expected):
        raise exceptions.PermissionDenied('File capability required.')
    return row, client


class CreateBridge(NarrowView):
    def post(self, request, run_id):
        from .runtime_services import get_internal_interaction_run
        run = get_internal_interaction_run(run_id=str(run_id), token=request.headers.get('X-Nexus-Interaction-Token', ''))
        if run.run_kind != 'invocation' or run.status != 'running' or run.caller_hidden_at:
            raise exceptions.PermissionDenied('Active invocation required.')
        data = request.data
        size, offset = data.get('size_bytes'), data.get('offset', 0)
        name, sha, mode = data.get('name'), data.get('sha256'), data.get('mode')
        if (type(size) is not int or not 0 < size <= 64 * 1024**3 or type(offset) is not int
                or not 0 <= offset <= size or mode not in ('upload', 'download')
                or not isinstance(name, str) or not re.fullmatch(r'[\w .()\-]{1,200}', name)
                or not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha)):
            raise exceptions.ValidationError('Invalid file metadata.')
        owner = runtime_identity(run)['owner_scope']
        live(SimpleNamespace(run=run, closed=False, expires_at=datetime.fromtimestamp(time.time() + TTL, dt_timezone.utc)))
        client = db()
        # Atomic bounded admission. Expiring ZSET members need no cleanup process.
        bid, now = str(uuid.uuid4()), time.time()
        quota = PREFIX + 'owner:' + owner
        admitted = client.eval("local k=KEYS[1]; redis.call('ZREMRANGEBYSCORE',k,'-inf',ARGV[1]); if redis.call('ZCARD',k)>=32 then return 0 end; redis.call('ZADD',k,ARGV[2],ARGV[3]); redis.call('EXPIRE',k,3600); return 1", 1, quota, now, now + TTL, bid)
        if not admitted: raise exceptions.Throttled(detail='Too many active file links.')
        ticket, worker = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        row = {'run': str(run.pk), 'owner': owner, 'mode': mode, 'size': size,
            'name': name, 'sha256': sha, 'expires': now + TTL, 'worker_hash': digest(worker)}
        client.set(key(bid), json.dumps(row), ex=TTL)
        client.set(key(bid, ':ticket'), digest(ticket), ex=600)
        client.set(key(bid, ':offset'), offset, ex=TTL)
        origin = str(settings.NEXUS_PUBLIC_BASE_URL).rstrip('/')
        if not origin.startswith('https://'): raise exceptions.APIException('HTTPS origin required.')
        return Response({'bridge_id': bid, 'url': origin + base(bid) + '#ticket=' + ticket,
            'link_expires_in': 600, 'session_expires_in': TTL, 'chunk_bytes': CHUNK,
            'size_bytes': size, 'sha256': sha, 'offset': offset,
            '_runtime': {'url': origin + base(bid) + 'runtime/', 'token': worker}})


class Page(NarrowView):
    def get(self, request, bridge_id):
        nonce = secrets.token_urlsafe(24)
        html = Path(__file__).with_name('file_bridge_page.html').read_text().replace('__NONCE__', nonce)
        response = HttpResponse(html)
        response['Content-Security-Policy'] = f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        response['X-Frame-Options'] = 'DENY'
        return response


class Redeem(NarrowView):
    def post(self, request, bridge_id):
        origin = request.headers.get('Origin')
        if origin and origin != str(settings.NEXUS_PUBLIC_BASE_URL).rstrip('/'):
            raise exceptions.PermissionDenied()
        if request.content_type != 'application/json': raise exceptions.PermissionDenied()
        row, client = load(bridge_id), db()
        token = request.data.get('ticket', '')
        if not isinstance(token, str): raise exceptions.PermissionDenied()
        secret = secrets.token_urlsafe(32)
        result = client.eval("if redis.call('GET',KEYS[1])~=ARGV[1] then return 0 end; redis.call('DEL',KEYS[1]); redis.call('SET',KEYS[2],ARGV[2],'EX',ARGV[3]); return 1", 2,
            key(bridge_id, ':ticket'), key(bridge_id, ':browser'), digest(token), digest(secret), max(1, int(row['expires'] - time.time())))
        if not result: raise exceptions.PermissionDenied('Link expired or already used. Request a new link through MCP.')
        response = Response({'mode': row['mode'], 'name': row['name'], 'size_bytes': row['size'],
            'sha256': row['sha256'], 'chunk_bytes': CHUNK, 'offset': int(client.get(key(bridge_id, ':offset')) or 0),
            'url': base(bridge_id) + 'file/'})
        response.set_cookie(COOKIE, secret, secure=True, httponly=True, samesite='Strict', path=base(bridge_id), max_age=max(1, int(row['expires'] - time.time())))
        return response


def exchange(bridge_id, operation, offset, length, body=None):
    client = db()
    row = load(bridge_id, client)
    # One request per file and eight global transfers; bound Redis payload memory.
    lock = client.lock(key(bridge_id, ':io'), timeout=35, blocking_timeout=3)
    if not lock.acquire(): raise exceptions.Throttled(detail='Another file request is active. Retry this range.')
    cid = str(uuid.uuid4())
    ck = key(bridge_id, ':cmd:' + cid)
    try:
        admitted = client.eval("redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',ARGV[1]); if redis.call('ZCARD',KEYS[1])>=8 then return 0 end; redis.call('ZADD',KEYS[1],ARGV[2],ARGV[3]); return 1", 1, PREFIX + 'io', time.time(), time.time()+35, cid)
        if not admitted: raise exceptions.Throttled(detail='File channels busy. Retry this range.')
        command = {'id': cid, 'operation': operation, 'offset': offset, 'length': length}
        if body is not None:
            command['sha256'] = hashlib.sha256(body).hexdigest()
            client.set(ck + ':data', body, ex=35)
        client.set(ck, json.dumps(command), ex=35)
        client.rpush(key(bridge_id, ':queue'), cid); client.expire(key(bridge_id, ':queue'), 35)
        response = client.blpop(ck + ':reply', timeout=25)
        if not response: raise exceptions.APIException('Agent file channel unavailable; resume with the same byte offset.')
        value = response[1]
        if not value or value[:1] != b'O': raise exceptions.APIException('Agent rejected file operation; request a fresh link or check transfer status.')
        return value[1:]
    finally:
        client.delete(ck, ck + ':data', ck + ':reply')
        client.zrem(PREFIX + 'io', cid)
        try: lock.release()
        except redis.exceptions.LockError: pass


class File(NarrowView):
    def get(self, request, bridge_id):
        row, client = authorized(request, bridge_id)
        common = {'Cache-Control': 'private, no-store', 'X-Nexus-File-SHA256': row['sha256'],
            'ETag': '"' + row['sha256'] + '"', 'Accept-Ranges': 'bytes',
            'Content-Disposition': content_disposition_header(True, row['name'])}
        if row['mode'] == 'upload':
            if request.method != 'HEAD': raise exceptions.PermissionDenied()
            return HttpResponse(headers={**common, 'X-Nexus-Upload-Offset': str(int(client.get(key(bridge_id, ':offset')) or 0)), 'Content-Length': '0'})
        start, length, status = 0, row['size'], 200
        selected = request.headers.get('Range')
        if selected and request.method != 'HEAD' and request.headers.get('If-Range', common['ETag']) == common['ETag']:
            parsed = selected_range(selected, row['size'])
            if parsed is None: return HttpResponse(status=416, headers={**common, 'Content-Range': f"bytes */{row['size']}"})
            start, length = parsed; status = 206
            common['Content-Range'] = f"bytes {start}-{start+length-1}/{row['size']}"
        common['Content-Length'] = str(length)
        if request.method == 'HEAD': return HttpResponse(headers=common, content_type='application/octet-stream')
        first = exchange(bridge_id, 'read', start, min(CHUNK, length))
        if len(first) != min(CHUNK, length): raise exceptions.APIException('Truncated file chunk.')
        def chunks():
            yield first
            offset = start + len(first)
            while offset < start + length:
                size = min(CHUNK, start + length - offset)
                chunk = exchange(bridge_id, 'read', offset, size)
                if len(chunk) != size: raise OSError('Truncated file chunk')
                yield chunk
                offset += len(chunk)
        async def achunks():
            yield first
            offset = start + len(first)
            while offset < start + length:
                size = min(CHUNK, start + length - offset)
                chunk = await sync_to_async(exchange, thread_sensitive=False)(bridge_id, 'read', offset, size)
                if len(chunk) != size: raise OSError('Truncated file chunk')
                yield chunk
                offset += len(chunk)
        response = StreamingHttpResponse(achunks() if isinstance(request._request, ASGIRequest) else chunks(), status=status, headers=common, content_type='application/octet-stream')
        response['X-Accel-Buffering'] = 'no'
        return response

    head = get

    def put(self, request, bridge_id):
        row, client = authorized(request, bridge_id)
        origin = request.headers.get('Origin')
        if origin and origin != str(settings.NEXUS_PUBLIC_BASE_URL).rstrip('/'): raise exceptions.PermissionDenied()
        try: size, offset = int(request.META.get('CONTENT_LENGTH', 0)), int(request.headers.get('X-Nexus-Upload-Offset', '-1'))
        except ValueError: raise exceptions.ValidationError('Invalid upload headers.') from None
        if row['mode'] != 'upload' or not 0 < size <= CHUNK or not 0 <= offset < row['size'] or offset + size > row['size']:
            raise exceptions.ValidationError('Invalid upload range.')
        data = request._request.read(size + 1)
        if len(data) != size or hashlib.sha256(data).hexdigest() != request.headers.get('X-Nexus-Chunk-SHA256'):
            raise exceptions.ValidationError('Chunk integrity mismatch.')
        reply = json.loads(exchange(bridge_id, 'write', offset, size, data))
        if reply.get('offset') != offset + size: raise exceptions.APIException('Upload acknowledgement mismatch.')
        client.eval("local a=tonumber(redis.call('GET',KEYS[1]) or '0'); if tonumber(ARGV[1])>a then redis.call('SET',KEYS[1],ARGV[1],'EX',ARGV[2]) end", 1,
            key(bridge_id, ':offset'), offset + size, max(1, int(row['expires'] - time.time())))
        return Response({'offset': offset + size, 'size_bytes': row['size']})


class Runtime(NarrowView):
    def get(self, request, bridge_id):
        _, client = authorized(request, bridge_id, worker=True)
        item = client.blpop(key(bridge_id, ':queue'), timeout=15)
        if not item: return Response({'command': None})
        command = client.get(key(bridge_id, ':cmd:' + item[1].decode()))
        return Response({'command': json.loads(command) if command else None})

    def delete(self, request, bridge_id):
        row, client = authorized(request, bridge_id, worker=True)
        client.delete(key(bridge_id), key(bridge_id, ':ticket'), key(bridge_id, ':browser'), key(bridge_id, ':offset'))
        client.zrem(PREFIX + 'owner:' + row['owner'], str(bridge_id))
        return Response({'revoked': True})


class Command(NarrowView):
    def get(self, request, bridge_id, command_id):
        _, client = authorized(request, bridge_id, worker=True)
        ck = key(bridge_id, ':cmd:' + str(command_id))
        command = client.get(ck)
        if not command or json.loads(command)['operation'] != 'write': raise exceptions.NotFound()
        data = client.get(ck + ':data')
        if data is None: raise exceptions.NotFound()
        return HttpResponse(data, content_type='application/octet-stream')

    def post(self, request, bridge_id, command_id):
        row, client = authorized(request, bridge_id, worker=True)
        ck = key(bridge_id, ':cmd:' + str(command_id))
        raw = client.get(ck)
        if not raw: raise exceptions.NotFound()
        command = json.loads(raw)
        try: size = int(request.META.get('CONTENT_LENGTH', 0))
        except ValueError: raise exceptions.ValidationError() from None
        if not 1 <= size <= CHUNK + 1: raise exceptions.ValidationError('Reply too large.')
        payload = request._request.read(size + 1)
        if len(payload) != size or payload[:1] not in (b'O', b'E'): raise exceptions.ValidationError()
        if payload[:1] == b'O' and command['operation'] == 'read' and size != command['length'] + 1: raise exceptions.ValidationError('Truncated chunk.')
        if not client.set(ck + ':answered', '1', nx=True, ex=35): raise exceptions.NotFound()
        client.rpush(ck + ':reply', payload); client.expire(ck + ':reply', 35)
        return Response({'accepted': True})
