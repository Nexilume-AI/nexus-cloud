# Production runtime contract

Nexus refuses to start with production settings unless the deployment supplies all of the following:

- PostgreSQL through `DATABASE_URL` or the `POSTGRES_*` variables. SQLite is development-only.
- Redis through an explicit `REDIS_URL`. The same Redis deployment backs Django's shared cache, distributed task locks, the Celery broker, and the result backend.
- `CELERY_TASK_ALWAYS_EAGER=0`.
- one explicit `NEXUS_PROCESS_ROLE` per process: `web`, `worker`, `beat`, or `management`. A combined process role is invalid.
- `CELERY_BEAT_SCHEDULER=config.beat.SingletonRedisScheduler`. It uses a renewable Redis leader lease, so a second Beat exits instead of duplicating scheduled financial work.
- an explicit `NEXUS_SHARED_STORAGE_ROOT` mounted read-write by every Web and Worker instance.
- `NEXUS_WEBSOCKET_ROUTING_MODE=sticky`. Computer Runtime, Terminal, and attached-browser sockets currently own live channels inside one ASGI process, so the ingress must preserve connection affinity. Durable state remains in PostgreSQL/Redis.

`DJANGO_DEBUG=0` activates the same checks even if `NEXUS_ENVIRONMENT=production` was accidentally omitted.

## Required process topology

Run migrations as a finite `management` process, then start independent services:

```text
web       NEXUS_PROCESS_ROLE=web     uvicorn config.asgi:application --host 0.0.0.0 --port 8000 --workers 1
worker    NEXUS_PROCESS_ROLE=worker  celery -A config.celery:app worker --loglevel=INFO
beat      NEXUS_PROCESS_ROLE=beat    celery -A config.celery:app beat --loglevel=INFO --pidfile= --schedule=<shared-root>/celerybeat-schedule
```

Scale Web and Worker as separate instances. Run exactly one Beat instance. Do not use Uvicorn's in-process worker fan-out for WebSocket traffic; scale one-worker containers/pods behind an affinity-aware ingress instead.

The reference topology is [infra/production/compose.yaml](../infra/production/compose.yaml). It intentionally exposes Web only to the deployment network so that TLS and sticky routing remain the ingress controller's responsibility.

## Readiness and consistency

Use `GET /api/v1/health/` only for process liveness. Use `GET /api/v1/health/readiness/` for load-balancer readiness.

Readiness verifies:

1. a live database query and PostgreSQL vendor;
2. a write/read/delete round trip through the shared Redis cache;
3. a fresh heartbeat that Beat enqueued and a dedicated Worker consumed;
4. the serving process has the `web` role;
5. shared storage is configured.

The heartbeat expires after 45 seconds by default. A missing Beat, missing Worker, unavailable Redis, or stalled task queue removes Web instances from readiness without turning the lightweight liveness endpoint into a dependency cascade.

For multiple hosts, `NEXUS_SHARED_STORAGE_ROOT` must be an RWX filesystem or equivalent shared persistent volume. Dataset object storage can still use the existing S3 backend, but Agent, Router, Provider Runtime, media, and other filesystem artifacts must not use pod-local disks.
