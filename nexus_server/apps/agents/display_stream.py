"""Caller-authenticated display event replay with Redis commit notifications."""
import asyncio
import json
import time
import redis
import redis.asyncio as redis_async
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from rest_framework import exceptions


def channel(run_id):
    return 'nexus:private-display:' + str(run_id)


def notify(run_id):
    try:
        with redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1) as client:
            client.publish(channel(run_id), 'changed')
    except redis.RedisError:
        pass  # Durable replay and the browser's fallback retain the events.


async def stream(request, run_id, cursor):
    from .services import get_private_display_run, private_display_events
    def replay():
        # Recheck caller, Run visibility and token on every notification/heartbeat.
        run = get_private_display_run(request=request, run_id=str(run_id))
        return private_display_events(run=run, cursor=cursor, limit=100)
    client = redis_async.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=3)
    pubsub = client.pubsub()
    deadline = time.monotonic() + 55
    try:
        await pubsub.subscribe(channel(run_id))
        while time.monotonic() < deadline:
            message = await pubsub.get_message(timeout=3)
            if message and message['type'] == 'subscribe':
                break
        yield ': connected\n\n'
        while time.monotonic() < deadline:
            while True:
                rows = await sync_to_async(replay)()
                for row in rows:
                    cursor = row['seq']
                    yield 'id: ' + str(cursor) + '\nevent: display\ndata: ' + json.dumps(row, cls=DjangoJSONEncoder) + '\n\n'
                if len(rows) < 100:
                    break
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=10)
            if not message:
                yield ': keep-alive\n\n'
    except exceptions.APIException:
        yield 'event: denied\ndata: {}\n\n'
    except (redis.RedisError, asyncio.TimeoutError):
        yield 'event: reconnect\ndata: {}\n\n'
    finally:
        await pubsub.aclose()
        await client.aclose()
