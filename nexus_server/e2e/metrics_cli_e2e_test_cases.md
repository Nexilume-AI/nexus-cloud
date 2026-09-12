# Metrics CLI E2E Test Cases

## Purpose

These tests verify monitoring commands through the real `nexus_client` CLI against local `nexus_server` on `http://127.0.0.1:8000`.

## How to Run

From `nexus_server/`:

```bash
python e2e/e2e_cli_metrics.py
```

## Test Cases

| Case | Command/API | Expected result |
| --- | --- | --- |
| Initialize CLI config | `nexus init` | Isolated CLI config is created. |
| Login | `nexus login` | Access and refresh tokens are returned. |
| Create tenant | `POST /api/v1/tenants/` | Tenant and owner membership are created. |
| Create API key fixture | `nexus key create` | API key id is available for metrics. |
| API key metrics | `nexus metrics show --resource api_key --id <key_id>` | Request/token/amount counters are returned. |
| System metrics | `nexus metrics show --resource system` | Tenant aggregate counts and usage are returned. |
| Alert create/list/delete | `nexus alert ...` | Alert is created, listed, and soft-deleted. |
| Report schedule create/list/delete | `nexus report schedule ...` | Schedule is created, listed, and deleted by email through CLI flow. |

## Data Source

The E2E script writes deterministic API key usage into the local database so the metrics command can assert non-zero counters.
