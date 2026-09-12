# OpenAPI Schema Constraints

## Scope

Nexus exposes a system OpenAPI schema for REST APIs.

Implemented endpoints:

- `GET /api/v1/schema/`
- `GET /api/v1/docs/swagger/`
- `GET /api/v1/docs/redoc/`

These endpoints are documentation/protocol endpoints and do not use the Nexus JSON response envelope.

## Tooling

OpenAPI generation uses `drf-spectacular`.

Required settings:

- `INSTALLED_APPS` includes `drf_spectacular`.
- `REST_FRAMEWORK.DEFAULT_SCHEMA_CLASS` is `apps.common.openapi.NexusAutoSchema`.
- `SPECTACULAR_SETTINGS.POSTPROCESSING_HOOKS` includes `apps.common.openapi.postprocess_schema`.

Validation command:

```bash
python manage.py spectacular --file openapi.yaml --validate
```

The generated schema currently validates with zero errors. Some duplicate operation id warnings remain because several compatibility endpoints are registered with both slash and no-slash variants.

## Response Envelope

JSON REST APIs use the Nexus envelope:

```json
{
  "ok": true,
  "data": {},
  "error": null,
  "request_id": "req_xxx"
}
```

Error envelope:

```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "ERROR_CODE",
    "message": "error message"
  },
  "request_id": "req_xxx"
}
```

OpenAPI components include envelope serializers for the first annotated module batch.

Envelope exceptions:

- OpenAPI schema/docs endpoints
- OpenAI-compatible endpoints
- Claude native endpoints
- MCP/SSE/streaming endpoints
- file download endpoints
- Prometheus `text/plain` endpoint

## Security Schemes

Schema includes:

- `BearerAuth`: `Authorization: Bearer <token>`
- `TenantHeader`: `X-Nexus-Tenant`
- `ProjectHeader`: `X-Nexus-Project`
- `ApiKeyHeader`: `X-Api-Key`

Bearer tokens can be:

- JWT user token
- `sk-nexus-...` API key
- `sa-nexus-...` service account token

## Sensitive Fields

The schema must not expose persisted secret fields such as:

- `encrypted_key`
- `encrypted_password`
- `key_hash`
- `token_hash`

Request-only credential fields such as login `password` may appear in request schemas and must be `writeOnly`.

One-time creation responses may include:

- `plaintext_key` for API key creation
- `plaintext_token` for service account token creation

These values are never stored by the server and are returned only once.

## Annotation Status

First annotated batch:

- `auth`
- `account`
- `tenancy`
- `iam`
- `api_keys`

`NexusAutoSchema` provides a generic envelope fallback for APIViews that have not yet been annotated. This keeps schema generation complete while allowing each module to be refined incrementally.

Remaining modules should be annotated in module-scoped passes:

- `providers`
- `deployments`
- `gateway`
- `routers`
- `agents`
- `datasets`
- `billing`
- `metrics`
- `audit`

Each pass should add:

- request serializer
- response serializer
- path/query/header parameters
- expected error codes
- tags
- explicit protocol exceptions for non-envelope endpoints

## Tests

OpenAPI tests must cover:

- `/api/v1/schema/` returns native OpenAPI, not Nexus envelope
- core paths exist
- security schemes exist
- stored secret fields are not exposed
