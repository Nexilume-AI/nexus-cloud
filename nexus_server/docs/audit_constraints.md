# Audit Module Constraints

## Scope

This module provides tenant-scoped audit log storage and query APIs.

Implemented APIs:

- `GET /api/v1/audit/logs/`
- `GET /api/v1/audit/logs/{id}/`

There is no current `nexus audit` CLI command in `nexus_client`, so verification is API-level only.

## Model

Implemented model:

- `AuditLog`

Fields:

- `tenant_id`
- `project_id`
- `actor_id`
- `action`
- `resource_type`
- `resource_id`
- `before_snapshot`
- `after_snapshot`
- `ip_address`
- `user_agent`
- `request_id`
- `metadata`
- `created_at`
- `updated_at`

`actor_id` is stored through the `actor` foreign key.

## Service Function

Primary service function:

```python
write_audit_log(actor, tenant, action, resource_type, resource_id, before=None, after=None, request=None)
```

Compatibility wrapper:

```python
log_audit(...)
```

Existing modules already call `log_audit`; it now delegates to `write_audit_log`.

`log_audit()` also accepts `before` and `after` for incremental adoption by existing write paths.

## Snapshot Policy

Audit snapshots are controlled allowlist snapshots, not raw database row dumps.

Create operations should write `after_snapshot`.

Update/status-change operations should write both `before_snapshot` and `after_snapshot`.

Delete/revoke/disable operations should write the pre-change state in `before_snapshot` and the resulting status in `after_snapshot`.

Read-only, invocation, download, and protocol events may omit snapshots and use `metadata` when no persisted resource state changes.

Snapshot helper:

```python
from apps.audit.snapshots import snapshot_resource
```

Sensitive fields must never be written to snapshots, including:

- plaintext API keys
- upstream provider keys
- passwords
- secrets
- tokens
- encrypted credentials
- key hashes

Safe identifiers such as `key_prefix` may be stored where needed for support and incident response.

Implemented resource snapshot allowlists:

- `api_key`: id, tenant/project, name, key prefix, status, creator, timestamps, policy limits and policy flags
- `provider_account`: id, tenant, provider, account id, URL, auth mode, login/quota status, pricing, creator, timestamps
- `deployment`: id, tenant, provider/account id, deployment id, model, endpoint, visibility scope, pricing, health/status fields

## Required Critical Actions

The audit API returns canonical action names for the current contract:

- `account.update`
- `team.create`
- `team.add_member`
- `project.create`
- `access.grant`
- `access.revoke`
- `billing.recharge`
- `billing.plan_buy`
- `api_key.create`
- `api_key.disable`
- `api_key.enable`
- `api_key.revoke`
- `provider_account.create`
- `provider_account.remove`
- `providers.account.pricing.set`
- `deployment.create`
- `deployment.update`
- `deployment.disable`
- `deployments.visibility.set`
- `deployments.pricing.set`
- `agent.create`
- `agent.deploy`
- `agent.stop`
- `agent.pricing.set`
- `agent.runtime.image.register`
- `agent.runtime.deploy`
- `agent.runtime.stop`
- `agent.runtime.invoke`
- `agent.runtime.health.healthy`
- `agent.runtime.health.unhealthy`
- `dataset.create`
- `dataset.delete`
- `router.deploy`
- `router.runtime.invoke`
- `alert.create`

Some existing modules store historical internal action names, such as `api_keys.create`. The API maps these to canonical names and supports filtering by canonical action.

## Permissions

Audit logs are visible only to users with audit permission for the tenant.

Tenant owners/admins satisfy this through `has_nexus_permission(..., "audit")`.

Regular tenant members cannot view audit logs.

Tenant isolation is enforced by `X-Nexus-Tenant`; users cannot query logs from tenants they do not belong to, and a tenant owner can only see logs where `tenant_id` matches the current tenant.

## Filtering

`GET /api/v1/audit/logs/` supports:

- `action`
- `resource_type`
- `resource_id`
- `actor` or `actor_id`
- `created_from` or `start`
- `created_to` or `end`

## Non-Goals

This module does not implement:

- immutable append-only database enforcement
- external SIEM export
- log retention policy
- cryptographic log signing
- full before/after snapshots for every existing write path outside the currently adopted modules

## Snapshot Adoption Status

Before/after snapshots are currently implemented for the first high-risk batch:

- `api_keys`
- `providers`
- `deployments`

Remaining modules should be adopted in later module-scoped passes:

- `agents`
- `routers`
- `datasets`
- `billing`
- `metrics`
- `tenancy`
- `iam`
