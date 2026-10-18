"""Durable, caller-owned telemetry with Redis wakeups; no model credentials in events."""
import asyncio
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from datetime import timedelta
from pathlib import Path, PurePosixPath

import redis
import redis.asyncio as redis_async
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions
from apps.common.crypto import encrypt_secret, decrypt_secret
from . import credential_services as credentials
from .models import AgentBackgroundJob, AgentBackgroundEvent, AgentCredentialAuthorization

MAX_BYTES = 24 * 1024 * 1024
TERMINAL = {'succeeded','failed','cancelled','timed_out','authorization_expired','interrupted'}
STATE_KEYS = {'job_id','status','created_at','started_at','finished_at','model_requests','last_provider_http_status','exit_code','error','artifacts','artifact_collection_error'}


def storage():
    # Outside MEDIA_ROOT: only the authenticated download view can serve these.
    root=getattr(settings,'NEXUS_PRIVATE_JOB_ROOT',None)
    if root is None:root=Path(settings.BASE_DIR).parent/'private-agent-jobs'
    return FileSystemStorage(location=root,file_permissions_mode=0o600,directory_permissions_mode=0o700)


def channel(jid):
    return 'nexus:background-job:' + str(jid)


def notify(jid):
    # Called after DB commit. Return a retryable error on failure: the producer
    # retries the same sequence, and duplicate ingestion wakes subscribers too.
    try:
        with redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2) as client:
            client.publish(channel(jid), 'changed')
    except redis.RedisError:
        raise exceptions.APIException('Event saved; delivery unavailable. Retry the same sequence.') from None


def register(run_id, token, data):
    run = credentials.internal_run(run_id, token)
    grant = credentials.internal_status(run_id, token, data.get('authorization_id'))
    if run.status != 'running' or grant['status'] != 'authorized':
        raise exceptions.PermissionDenied('Active authorization required.')
    try: request_id = uuid.UUID(str(data.get('request_id')))
    except ValueError: raise exceptions.ValidationError('Invalid request ID.') from None
    with transaction.atomic():
        authorization = AgentCredentialAuthorization.objects.select_for_update().get(pk=grant['authorization_id'])
        if AgentBackgroundJob.objects.filter(authorization=authorization).count() >= 128 and not AgentBackgroundJob.objects.filter(authorization=authorization,request_id=request_id).exists():
            raise exceptions.Throttled(detail='Background job quota reached.')
        job, _ = AgentBackgroundJob.objects.get_or_create(authorization=authorization, request_id=request_id, defaults={
            'push_secret': encrypt_secret(secrets.token_urlsafe(32)), 'expires_at':timezone.now()+timedelta(hours=2)})
    base = str(settings.NEXUS_PUBLIC_BASE_URL).rstrip('/') + '/api/v1/agent-background-jobs/' + str(job.id) + '/'
    return {'job_ref':str(job.id),'push_url':base+'ingest/','command_url':base+'commands/',
            'artifact_url':base+'artifacts/', 'view_url':base+'page/', 'token':decrypt_secret(job.push_secret)}


def producer(jid, token, lock=False):
    query = AgentBackgroundJob.objects.select_for_update() if lock else AgentBackgroundJob.objects
    job = query.filter(pk=jid).first()
    if not job or job.expires_at <= timezone.now() or not token or not hmac.compare_digest(decrypt_secret(job.push_secret), token):
        raise exceptions.PermissionDenied('Job delivery authorization unavailable.')
    return job


def owner(request, jid):
    job = AgentBackgroundJob.objects.filter(pk=jid).first()
    if not job: raise exceptions.NotFound()
    credentials.browser_row(request, job.authorization_id)
    return job


def artifact_path(value):
    if not isinstance(value,str) or not value or len(value)>512 or '\\' in value or any(ord(c)<32 for c in value):
        raise exceptions.ValidationError('Invalid artifact path.')
    if PurePosixPath(value).is_absolute() or any(p in ('','.', '..') or p.startswith('.') for p in value.split('/')):
        raise exceptions.ValidationError('Invalid artifact path.')
    return value


def validate_event(event):
    if not isinstance(event,dict) or set(event)!={'seq','kind','data','time'}:
        raise exceptions.ValidationError('Invalid event.')
    if type(event['seq']) is not int or not 1 <= event['seq'] <= 12000:
        raise exceptions.ValidationError('Invalid event sequence.')
    if not isinstance(event['time'],(int,float)) or not isinstance(event['data'],dict):
        raise exceptions.ValidationError('Invalid event data.')
    kind, data = event['kind'], event['data']
    if kind == 'state':
        if set(data)-STATE_KEYS: raise exceptions.ValidationError('Unsupported state fields.')
        if 'status' in data and data['status'] not in TERMINAL|{'queued','running','collecting'}:
            raise exceptions.ValidationError('Invalid job status.')
        manifest=data.get('artifacts',[])
        if not isinstance(manifest,list) or len(manifest)>256: raise exceptions.ValidationError('Invalid manifest.')
        total=0
        for item in manifest:
            if not isinstance(item,dict) or set(item)!={'path','bytes','sha256'}: raise exceptions.ValidationError('Invalid artifact.')
            artifact_path(item['path'])
            if type(item['bytes']) is not int or not 0<=item['bytes']<=16*1024*1024 or not re.fullmatch('[a-f0-9]{64}',str(item['sha256'])):
                raise exceptions.ValidationError('Invalid artifact size/hash.')
            total+=item['bytes']
        if total>64*1024*1024: raise exceptions.ValidationError('Artifact quota exceeded.')
    elif kind == 'log':
        if set(data)!={'text'} or not isinstance(data['text'],str) or len(data['text'].encode())>16384:
            raise exceptions.ValidationError('Invalid log chunk.')
    elif kind == 'stage':
        if set(data)!={'name'} or data['name'] not in ('reference_measurement_started','reference_measurement_completed','optimizer_started'):
            raise exceptions.ValidationError('Invalid stage.')
    elif kind == 'delivery':
        if set(data)!={'complete'} or data['complete'] is not True: raise exceptions.ValidationError('Invalid delivery state.')
    else: raise exceptions.ValidationError('Unsupported event kind.')
    try: size=len(json.dumps(event,allow_nan=False).encode())
    except (TypeError,ValueError): raise exceptions.ValidationError('Invalid event JSON.') from None
    if size>(262144 if kind=='state' else 65536): raise exceptions.ValidationError('Event too large.')
    return size


def ingest(jid, token, events):
    if not isinstance(events,list) or not 1<=len(events)<=32: raise exceptions.ValidationError('Invalid batch.')
    sizes=[validate_event(event) for event in events]
    with transaction.atomic():
        job=producer(jid,token,lock=True)
        for event,size in zip(events,sizes):
            if event['seq']<=job.last_seq:
                old=job.events.filter(seq=event['seq']).first()
                if not old or old.payload!=event: raise exceptions.ValidationError('Sequence conflict.')
                continue
            if event['seq']!=job.last_seq+1: raise exceptions.ValidationError('Sequence gap; replay from the last acknowledged event.')
            if job.byte_count+size>MAX_BYTES: raise exceptions.ValidationError('Event quota exceeded.')
            AgentBackgroundEvent.objects.create(job=job,seq=event['seq'],payload=event)
            job.last_seq=event['seq'];job.byte_count+=size
            if event['kind']=='state':
                if job.state.get('status') in TERMINAL and event['data'].get('status',job.state['status'])!=job.state['status']:
                    raise exceptions.ValidationError('Terminal state cannot change.')
                if job.state.get('status') in TERMINAL and 'artifacts' in event['data'] and event['data']['artifacts']!=job.state.get('artifacts',[]):
                    raise exceptions.ValidationError('Terminal manifest cannot change.')
                job.state.update(event['data'])
            if event['kind']=='delivery':job.state['delivery_complete']=True
        job.save(update_fields=['last_seq','byte_count','state'])
    notify(job.id)
    return {'ack':job.last_seq,'cancel_requested':job.cancel_requested}


def snapshot(job):
    return {'id':str(job.id),'state':job.state,'last_seq':job.last_seq,'cancel_requested':job.cancel_requested,
            'artifacts':[{**{k:v for k,v in item.items() if k!='storage_name'},'path':path} for path,item in job.artifacts.items()]}


def upload(jid, token, path, file):
    path=artifact_path(path)
    if not file or file.size>16*1024*1024: raise exceptions.ValidationError('Artifact too large.')
    digest=hashlib.sha256()
    for chunk in file.chunks():digest.update(chunk)
    checksum=digest.hexdigest();file.seek(0)
    with transaction.atomic():
        job=producer(jid,token,lock=True)
        item=next((item for item in job.state.get('artifacts',[]) if item['path']==path),None)
        if not item or item['bytes']!=file.size or item['sha256']!=checksum:
            raise exceptions.ValidationError('Artifact does not match the terminal manifest.')
        if path not in job.artifacts:
            if len(job.artifacts)>=256 or sum(value['bytes'] for value in job.artifacts.values())+file.size>64*1024*1024:
                raise exceptions.ValidationError('Artifact quota exceeded.')
            name=storage().save('private-agent-jobs/'+str(job.id)+'/'+uuid.uuid4().hex,file)
            try:
                job.artifacts[path]={'bytes':file.size,'sha256':checksum,'storage_name':name}
                job.save(update_fields=['artifacts'])
            except Exception:
                storage().delete(name);raise
    notify(job.id)
    return {'uploaded':path}


def replay(jid, after):
    return list(AgentBackgroundEvent.objects.filter(job_id=jid,seq__gt=after).values_list('payload',flat=True)[:128])


async def stream(jid, after=0, commands=False):
    """Subscribe before replay to close the commit/subscribe race; no DB polling."""
    client=redis_async.Redis.from_url(settings.REDIS_URL,socket_connect_timeout=3)
    pubsub=client.pubsub();deadline=time.monotonic()+55
    try:
        await pubsub.subscribe(channel(jid))
        # Wait for Redis subscription acknowledgement before reading persistent state.
        while True:
            message=await pubsub.get_message(timeout=3)
            if message and message['type']=='subscribe':break
            if time.monotonic()>deadline:return
        dirty=True
        while time.monotonic()<deadline:
            if dirty:
                if commands:
                    job=await sync_to_async(AgentBackgroundJob.objects.get)(pk=jid)
                    if job.cancel_requested:
                        yield 'event: cancel\ndata: {}\n\n';return
                else:
                    while True:
                        events=await sync_to_async(replay)(jid,after)
                        for event in events:
                            after=event['seq']
                            yield 'id: '+str(after)+'\nevent: job\ndata: '+json.dumps(event,allow_nan=False)+'\n\n'
                        if len(events)<128:break
                    job=await sync_to_async(AgentBackgroundJob.objects.get)(pk=jid)
                    yield 'event: snapshot\ndata: '+json.dumps(snapshot(job))+'\n\n'
                dirty=False
            message=await pubsub.get_message(ignore_subscribe_messages=True,timeout=15)
            if message:dirty=True
            else:yield ': keep-alive\n\n'
    except (redis.RedisError, asyncio.TimeoutError):
        yield 'event: reconnect\ndata: {}\n\n'
    finally:
        await pubsub.aclose();await client.aclose()
