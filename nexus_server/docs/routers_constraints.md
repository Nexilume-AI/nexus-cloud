# Routers Module Constraints

## Scope

This module implements cross-Model-Pool routing and router hosting APIs required by the current CLI:

- `nexus router create <name>`
- `nexus router list`
- `nexus router use <router_id>`
- `nexus router upload <router_id> router.py`
- `nexus router info <router_id>`
- `nexus router deploy <router_id>`
- `nexus router bind-model-group <router_id> <provider0> <provider1> ...`
- `nexus router policy set <router_id> --strategy cost|quality|custom`
- `nexus router pricing set <router_id> --plan <plan_id>`

`router use` is local CLI profile state and does not call the server.

## API Contract

Implemented routes:

- `GET /api/v1/routers/`
- `POST /api/v1/routers/`
- `GET /api/v1/routers/{id}/`
- `PATCH /api/v1/routers/{id}/`
- `GET /api/v1/routers/{id}/source/`
- `POST /api/v1/routers/{id}/upload/`
- `POST /api/v1/routers/{id}/deploy/`
- `POST /api/v1/routers/{id}/export-credentials/`
- `POST /api/v1/routers/{id}/test/`
- `POST /api/v1/routers/{id}/model-groups/`
- `POST /api/v1/routers/{id}/policy/`
- `POST /api/v1/routers/{id}/pricing/`

All routes require `Authorization: Bearer <token>` and `X-Nexus-Tenant: <tenant_id>`.

`POST /api/v1/routers/{id}/test/` performs a dry-run route selection. It returns the selected model pool and selected source without calling the upstream provider and without writing a gateway request log.

`POST /api/v1/routers/{id}/export-credentials/` creates a router-scoped gateway API key and returns client-ready access details. The generated key must have `APIKeyPolicy.allowed_routers=[router_id]` and must not grant unrestricted gateway access. The response includes:

- OpenAI-compatible gateway base URL
- chat completions URL
- router invoke URL
- plaintext gateway key, shown only once
- recommended model
- `.env`, OpenAI-compatible `curl`, and router invoke `curl` snippets

## Web Product Surface

The Web console must present Routers as a cross-Model-Pool resource list.

The primary page must show only:

- `Router List`

All other router operations must open from row actions or contextual controls in dialogs:

- create router
- configure router name, cross-pool strategy, custom router.py activation, and Model Pool bindings
- export access
- advanced custom `router.py` management, only from the `Custom router.py` strategy state

The main workflow must not show provider account preferences, pool contribution selectors, source/provider controls, selected-router side panels, route test actions, or always-visible builder/test/advanced panels. Those belong to Model Pool source routing, Community Pool trust surfaces, internal diagnostics, or row action dialogs.

`Custom router.py` management must remain available only inside an `Advanced` dialog, and the Web console must only show that entry point after the user selects the `Custom router.py` strategy in `Configure router`. Legacy provider filter controls must not be exposed in the Web console.

The `Advanced` dialog must load `router.py` from the server. It must not imply that a frontend template is the current runtime source. `GET /api/v1/routers/{id}/source/` returns the active deployed source when available; otherwise it returns the current uploaded draft or an empty source state.

The `Configure router` dialog must expose `Custom router.py` as a strategy option. Selecting it must clearly state that only the deployed `router.py` will execute. The UI must block applying `custom` when no deployed `router.py` is available. Built-in strategies must clearly state that `router.py` will not execute.

Router rows must expose `Export access` for deployed routers. Exported credentials are shown in an in-page dialog, not downloaded as a file.

The `Configure router` dialog must not show raw invoke endpoints. Endpoint URLs, API keys, environment snippets, and curl examples belong only in the `Export access` dialog so configuration and access distribution stay separate.

## Models

Implemented models:

- `Router`
- `RouterVersion`
- `RouterModelGroupBinding`
- `RouterPricing`
- `RouterDeployment`
- `RouterRuntimeInvocation`

## Router Upload MVP

`upload` accepts multipart upload of a file named exactly `router.py`.

Files are stored under:

```text
nexus_server/storage/routers/<tenant_id>/<router_id>/
```

The server validates that the uploaded file:

- is named exactly `router.py`
- is UTF-8 Python source
- defines a synchronous `route(request, candidates, context)` function
- exists before deploy

`GET /api/v1/routers/{id}/source/` is read-only and path-confined to `NEXUS_ROUTER_STORAGE_ROOT`. It returns at most the current displayable UTF-8 source, version metadata, and whether the source is the active runtime source.

## Deployment MVP

`deploy` requires at least one uploaded `RouterVersion`.

Deployment creates a `RouterDeployment` with status `active` and marks the router `deployed`.

## Model Group Bindings

Routers select between model pools. They must not directly manage provider accounts, provider runtimes, or source endpoints.

The preferred binding payload is:

```json
{"model_group_ids": ["<model-group-uuid>"]}
```

These bindings set `provider_name="*"` and point `RouterModelGroupBinding.model_group` to the selected `ModelGroup`.

For CLI compatibility, `bind-model-group` still accepts provider values in these forms:

- `openai:smart-chat`
- `qwen/chip-codex`
- `deepseek`

Legacy provider bindings are interpreted as provider filters within a named model group for API/CLI compatibility only. The Web console must bind model pools by id and must not expose legacy provider binding inputs.

`bind-model-group` accepts provider values in these forms:

- `openai:smart-chat`
- `qwen/chip-codex`
- `deepseek`

When only one token is provided, it is stored as both `provider_name` and `model_group_name`.

Bindings are upserted and ordered by input position through `priority`.

## Policy

Supported strategies:

- `manual_priority`
- `lowest_cost_pool`
- `lowest_latency_pool`
- `best_health_pool`
- `task_type`
- `custom`
- `cost` (legacy alias for cost-oriented pool ordering)
- `quality` (legacy alias for health-oriented pool ordering)

Non-custom strategies first choose an ordered list of model pools, then each model pool applies its own source routing policy. `custom` requires a deployed `router.py` version. Runtime calls execute the deployed file through the router runtime sandbox and receive deployment candidates produced from router-bound model pools.

The normal Web UI should expose only:

- `Priority` (`manual_priority`)
- `Lowest cost` (`lowest_cost_pool`)
- `Lowest latency` (`lowest_latency_pool`)
- `Best health` (`best_health_pool`)
- `Custom router.py` (`custom`, requires a deployed `router.py`)

`task_type`, `cost`, and `quality` are advanced or compatibility strategies.

## Custom Router Runtime Contract

Custom `router.py` must expose this callable interface:

```python
from typing import Any

def route(
    request: dict[str, Any],
    candidates: list[dict[str, Any]],
    context: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ordered_deployment_ids": ["deployment_uuid_or_deployment_id"],
        "reason": "optional human-readable reason",
        "metadata": {},
    }
```

Runtime inputs:

- `request`: sanitized chat completion payload; key-like fields such as `api_key`, `secret`, `password`, `token`, and `provider_key` are redacted
- `candidates`: active, healthy router-bound deployments only; provider secrets and account keys are never included
- `context`: `tenant_id`, `project_id`, `router_id`, `router_version`, `request_id`, `api_key_id`, and `strategy`

For OpenAI-compatible multimodal requests, custom routers may inspect `messages[].content` arrays. Nexus redacts image data URLs to `[REDACTED_DATA_URL]` and replaces signed URL query strings with `[REDACTED_QUERY]` before the payload enters the router sandbox.

Example multimodal-aware router:

```python
def route(request, candidates, context):
    has_image = any(
        isinstance(message.get("content"), list)
        and any(part.get("type") == "image_url" for part in message["content"])
        for message in request.get("messages", [])
    )
    vision = [candidate for candidate in candidates if "vision" in candidate.get("model", "")]
    selected = vision[0] if has_image and vision else candidates[0]
    return {"ordered_deployment_ids": [selected["id"]], "reason": "multimodal-aware route"}
```

Runtime output:

- `ordered_deployment_ids` must be non-empty
- each value must match either `candidate["id"]` or `candidate["deployment_id"]`
- selecting an endpoint, provider account, provider key, or arbitrary model outside `candidates` is rejected

Invalid output returns `ROUTER_RUNTIME_INVALID_DECISION`.

## Sandbox

`custom` runtime execution is isolated by `nsjail` when `NEXUS_ROUTER_RUNTIME_RUNNER=nsjail`.

Runtime settings:

- `NEXUS_ROUTER_RUNTIME_RUNNER`: `nsjail`, `docker-nsjail`, `local`, or `disabled`; production default is `nsjail`
- `NEXUS_NSJAIL_BIN`
- `NEXUS_ROUTER_RUNTIME_TIMEOUT_SECONDS`
- `NEXUS_ROUTER_RUNTIME_CPU_SECONDS`
- `NEXUS_ROUTER_RUNTIME_MEMORY_MB`
- `NEXUS_ROUTER_RUNTIME_PIDS_LIMIT`
- `NEXUS_ROUTER_RUNTIME_PYTHON`
- `NEXUS_ROUTER_RUNTIME_DOCKER_NSJAIL_IMAGE`
- `NEXUS_ROUTER_RUNTIME_READONLY_BINDS`

The nsjail runner creates a per-invocation temporary root, mounts only the runtime work directory as writable, mounts configured Python/runtime paths read-only, redacts secrets from runtime input, and executes `runtime_entrypoint.py` with no provider credentials in the environment.

`local` executes uploaded code in-process and is only for tests or trusted local development.

`docker-nsjail` is intended for Windows integration tests. It runs Docker image `nsjail-test`, mounts the temporary router runtime work directory, and executes nsjail inside the Linux container.

## Pricing

Pricing stores a `plan_id` and optional `pricing_json`. The current CLI sends `--plan <plan_id>`.

## Permissions

Tenant owners/admins can create and manage routers.

Router creators can manage routers they created.

Other users can view routers only through IAM access grants. A read grant does not allow deploy/upload/policy/pricing changes.

## Auditing

Write operations create `AuditLog` entries:

- `routers.create`
- `routers.update`
- `routers.upload`
- `routers.deploy`
- `routers.model_groups.bind`
- `routers.policy.set`
- `routers.pricing.set`
- `routers.runtime.invoke`

Custom runtime attempts also create tenant-scoped `RouterRuntimeInvocation` rows.

## Non-Goals

This module does not implement:

- live health checks
- model-group existence validation
- pricing charge enforcement
