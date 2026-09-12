# Deployment Health Constraints

## Scope

Deployment health checks are now real provider reachability checks, not just database status reads.

Supported paths:

- `GET /api/v1/deployments/status/`
- `POST /api/v1/deployments/{id}/health-check/`

CLI support:

- `nexus deployment status`
- `nexus deployment health-check <deployment_id>`

## Health Fields

`Deployment` stores the latest health snapshot:

- `health_status`: `unknown`, `healthy`, `degraded`, `unhealthy`
- `health_reason`
- `last_checked_at`
- `last_success_at`
- `last_failure_at`
- `consecutive_failures`
- `last_latency_ms`

`DeploymentHealthCheck` stores historical check results.

## Probe Behavior

The health checker uses the deployment endpoint if present, otherwise the provider account URL.

For OpenAI-compatible providers:

1. Try `GET <base_url>/models`.
2. If `/models` returns `404` or `405`, try a minimal chat completion request.

Local test hosts:

- `mock.local` and `mock.provider.local` return `healthy`.
- `fail.local` and `fail.provider.local` return `unhealthy`.

Secrets are decrypted only for upstream authorization and are never returned by APIs or stored in health result records.

## Runtime Integration

Gateway and router runtime candidate selection now uses deployment health:

- `healthy` deployments are preferred.
- `unknown` deployments remain usable as fallback candidates.
- `degraded` deployments remain usable after `unknown`.
- `unhealthy` deployments are skipped.

Runtime provider success marks a deployment `healthy`.

Runtime provider failures mark a deployment `degraded`; after three consecutive runtime failures it becomes `unhealthy`.

## Tasks

Celery tasks:

- `apps.deployments.tasks.check_one_deployment_health`
- `apps.deployments.tasks.check_all_active_deployments`

Celery beat schedule:

- `check-active-deployment-health`
- interval defaults to `NEXUS_DEPLOYMENT_HEALTH_INTERVAL_SECONDS=300`

If Celery is not installed in the local test environment, the task decorator falls back to a no-op wrapper so Django tests can still run.

## Audit

Health checks write audit entries only on important state changes:

- `deployment.health.healthy`
- `deployment.health.unhealthy`

Routine repeated checks that do not change state do not write audit logs.

## Current Non-Goals

This MVP does not implement:

- provider-specific health adapters beyond OpenAI-compatible probing
- circuit breaker half-open windows
- distributed health-check locking
- automatic provider account health rollup
- health history retention cleanup
