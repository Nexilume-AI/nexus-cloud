# Jobs / Celery Runtime Constraints

## Scope

The `apps.jobs` module is the shared async execution surface for long-running Nexus operations. The first integrated producer is `apps.agents` runtime deploy/stop/health-check. Provider, dataset, billing, and router async conversion should reuse this module in later phases.

## API

- All endpoints are under `/api/v1/`.
- REST responses use the unified envelope.
- Job queries require `Authorization: Bearer <token>` and `X-Nexus-Tenant: <tenant_id>`.
- Current endpoints:
  - `GET /api/v1/jobs/`
  - `GET /api/v1/jobs/{job_id}/`
  - `GET /api/v1/jobs/{job_id}/events/`
- Query filters:
  - `job_type`
  - `status`
  - `resource_type`
  - `resource_id`

## Tenant Isolation

- `Job` and `JobEvent` both have a required `tenant` foreign key.
- Query APIs always resolve `X-Nexus-Tenant` through tenant visibility and filter by that tenant.
- Job events are always written with the same tenant as the parent job.

## Audit

Every write path records audit logs:

- `jobs.create`
- `jobs.start`
- `jobs.succeed`
- `jobs.fail`

Agent runtime worker writes also record runtime state transitions:

- `agents.runtime.deploy`
- `agents.runtime.deploy.succeeded`
- `agents.runtime.deploy.failed`
- `agents.runtime.stop`
- `agents.runtime.health.healthy`
- `agents.runtime.health.unhealthy`

## Secret Handling

Job `input_json`, `result_json`, event metadata, and audit snapshots must pass through recursive redaction before storage. Keys containing `password`, `secret`, `token`, `api_key`, `apikey`, `key`, or `credential` are stored as `***REDACTED***`, except explicitly safe fields such as `key_prefix`, `token_prefix`, `public_key`, `model_key`, and `idempotency_key`.

## Celery Behavior

- `CELERY_TASK_ALWAYS_EAGER=1` remains the default for local tests.
- Production refuses to start unless `CELERY_TASK_ALWAYS_EAGER=0`, Redis backs the broker/result backend and shared cache, and Web/Worker/Beat use separate `NEXUS_PROCESS_ROLE` values. See `production_runtime.md`.
- Runtime APIs create the database state first, then enqueue a Celery task.
- In eager mode, the API response usually contains the final runtime state.
- In non-eager mode, clients should poll `/api/v1/jobs/{job_id}/` and the resource-specific status endpoint.

## Agent Runtime Integration

- `POST /api/v1/agents/{agent_id}/runtime/deployments/` returns the runtime deployment plus `job_id`.
- `POST /api/v1/agents/{agent_id}/runtime/deployments/stop/` returns the runtime deployment plus `job_id`.
- `POST /api/v1/agents/{agent_id}/runtime/health-check/` returns the runtime deployment plus `job_id`.
- The actual Docker/fake runner work is executed by Celery task functions that call shared service functions.
- Worker-side MCP public URLs are built from `NEXUS_PUBLIC_BASE_URL` when no HTTP request object exists.

## Later Phases

The same job model should be used for:

- Provider runtime build/start/login health probes.
- Dataset object-storage upload indexing and OpenSearch indexing.
- Billing payment reconciliation and notification tasks.
- Router sandbox validation and heavier route test runs.
