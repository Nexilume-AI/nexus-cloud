from __future__ import annotations

import uuid

from celery.beat import PersistentScheduler
from django.conf import settings
from redis import Redis


BEAT_LEADER_KEY = "nexus:runtime:celery-beat-leader:v1"
BEAT_LEADER_TTL_SECONDS = 30


class SingletonRedisScheduler(PersistentScheduler):
    """Celery Beat scheduler guarded by a renewable Redis leader lease."""

    def __init__(self, *args, **kwargs):
        self._leader_token = uuid.uuid4().hex
        self._leader_redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        super().__init__(*args, **kwargs)

    def setup_schedule(self) -> None:
        acquired = self._leader_redis.set(
            BEAT_LEADER_KEY,
            self._leader_token,
            nx=True,
            ex=BEAT_LEADER_TTL_SECONDS,
        )
        if not acquired:
            raise RuntimeError("Another Nexus Celery Beat instance already holds the Redis leader lease.")
        try:
            super().setup_schedule()
        except Exception:
            self._release_leader_lease()
            raise

    def tick(self, *args, **kwargs):
        if not self._refresh_leader_lease():
            raise RuntimeError("Nexus Celery Beat lost its Redis leader lease; scheduling stopped.")
        return min(float(super().tick(*args, **kwargs)), BEAT_LEADER_TTL_SECONDS / 3)

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._release_leader_lease()

    def _refresh_leader_lease(self) -> bool:
        result = self._leader_redis.eval(
            """
            if redis.call('get', KEYS[1]) == ARGV[1] then
                return redis.call('expire', KEYS[1], ARGV[2])
            end
            return 0
            """,
            1,
            BEAT_LEADER_KEY,
            self._leader_token,
            BEAT_LEADER_TTL_SECONDS,
        )
        return bool(result)

    def _release_leader_lease(self) -> None:
        try:
            self._leader_redis.eval(
                """
                if redis.call('get', KEYS[1]) == ARGV[1] then
                    return redis.call('del', KEYS[1])
                end
                return 0
                """,
                1,
                BEAT_LEADER_KEY,
                self._leader_token,
            )
        except Exception:
            pass
