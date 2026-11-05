# Model capacity and reasoning discovery

`GET /api/v1/openai/v1/model/info` implements LiteLLM v1 model discovery.
The unversioned alias `/api/v1/openai/model/info` and both trailing-slash forms
are also available. Set clients' inference base URL to
`https://cloud.nexilume.com/api/v1/openai/v1`.
This is a discovery compatibility layer, not a LiteLLM administration server.
The existing `/models` response and inference protocols are unchanged.
Send the same Gateway credential as for inference. Optional filters are
`model` (or `model_name`) and `router_id`. Unknown or inaccessible selections
return 404. The response is `private, no-store` and contains no credentials,
physical endpoints or deployment identifiers.

### LiteLLM v1 contract

Rows contain `model_name`, `litellm_params` and `model_info`. `model_info.id`
is an opaque, deterministic HMAC of the authorized logical model, tenant and
pool identities. It is stable across restarts; rotating the server signing
secret or changing a model's pool graph changes it. It is not a physical
deployment ID. `litellm_model_id` filters this directory and returns 400 when
unknown or inaccessible. Name filters return 404. All failures use the
OpenAI-style `error.message/type/code/param` envelope and HTTP status codes.

`litellm_params.model` is `openai/<visible-alias>` and
`custom_llm_provider` is `openai`: these describe the Nexus OpenAI-compatible
facade, not the private upstream. `api_base`, credentials and hidden physical
model names are never exposed. `base_model` and prices remain null because the
Router may dispatch across different models and billing is not inferred from
community reference prices. `mode` is `chat` only when every candidate supports
chat, otherwise null. `supports_reasoning` mirrors the agreed capability.

`max_input_tokens` uses each route's tighter known independent input ceiling or
shared context window, then takes the minimum across all routes. If a route has
neither, the result is null. The legacy LiteLLM `max_tokens` aliases that input
capacity; it is **not** an output default. `max_output_tokens` remains separate.
The original independent input observation is preserved in
`nexus.fields.max_input_tokens`; `input_capacity_from_context` explains fallback.
Shared-context capacity still requires room for output; clients must reserve it.

`include_team_models` is accepted as a boolean but never broadens Nexus access.
`healthy_only=true` omits logical models whose candidates are all explicitly
unhealthy (unknown health remains visible); direct ID lookup ignores that flag.
`teamId` is rejected with 400: LiteLLM teams are not Nexus Organizations.
`/v2/model/info`, `/key/info`, `/team/info` and model-management mutations are
outside this compatibility layer. Inspect tolerates unavailable key/team alias
management because callable Nexus aliases already appear in the directory.

```json
{
  "data": [{
    "model_name": "router-visible-alias",
    "litellm_params": {"model": "openai/router-visible-alias", "custom_llm_provider": "openai"},
    "model_info": {
      "id": "opaque-stable-logical-model-id",
      "base_model": null,
      "mode": "chat",
      "max_tokens": null,
      "input_cost_per_token": null,
      "output_cost_per_token": null,
      "supports_reasoning": null,
      "context_window": null,
      "max_input_tokens": null,
      "max_output_tokens": null,
      "recommended_output_tokens": null,
      "reasoning": {"supported": null, "efforts": null, "included_in_output": null}
    },
    "nexus": {
      "schema_version": 1,
      "aggregation": "minimum_across_candidates_unknown_if_any_unknown",
      "fields": {}
    }
  }]
}
```

Each `nexus.fields` item supplies `value`, `sources`, `updated_at`, `stale`, and
`upstream_confirmed`. A reference value is useful for configuration but is not a
guarantee about an upstream deployment. `upstream_confirmed` means reported by a
recent upstream metadata response, not validated by a maximum-sized inference.

## Sources and routing

Provider catalog refresh retains typed capacity fields from `/models`, then
optionally reads `/model/info` once with the same credential. The optional request
has a five-second timeout, two-MiB bound and rejects redirects. Its failure does
not change Provider health or advertised model IDs. Cached observations are tied
to the runtime endpoint and become stale after 24 hours.

Per-field priority: explicit administrator values, upstream observations, exact
models.dev Provider/model match, then unknown (`null`). Administrator limits are
clamped by any known upstream hard limit. A Canonical Model's `context_window`
is an administrator declaration; its `metadata.capacity_override` can supply
other normalized fields. No substring, family or case-insensitive model matching
is used. Router-visible aliases are resolved through their actual Model Pools.
Across possible routes, every candidate must supply a field before a common
value can be advertised. Numeric limits use the minimum, reasoning effort lists
use the intersection. These are discovery results, not a change to inference
admission, accounting, user output budgets or routing policies.

For an explicit models.dev identity mapping, set a model offer's metadata to:

```json
{"models_dev": {"provider": "minimax", "model": "MiniMax-M3.1-Flash"}}
```

Otherwise `Provider.name` and `Deployment.upstream_model_id` are used exactly.
`NEXUS_MODELS_DEV_PROVIDER_MAP` is an optional JSON map for administrator-defined
Provider-name mappings. Never map an arbitrary multi-provider gateway to one lab
without checking its deployments. Source-specific mappings are safer.

### Configure references in the console

Open **Providers → Models → Model capabilities**. Search the cached models.dev
Provider IDs, select a provider, then search and select an exact model ID.
The preview shows reference context/input/output capacity and catalog timestamp.
**Enter IDs manually** accepts an exact pair even when it is absent from the
cache; an unmatched identity never invents capacity. **Restore default** removes
the Provider model mapping. Discovery refresh preserves the mapping.

Sources inherit their Provider model mapping. **Model Pool → Sources → Model
capacity reference** can override it for one Source; **Restore inheritance**
removes that override. These metadata identities do not change upstream model
IDs, routing, credentials, pricing, or inference budgets. Administrator/upstream
priority and conservative Router aggregation still apply.

Authenticated console endpoints (not Gateway model credentials):
- `GET /api/v1/model-reference-catalog/?q=...&provider=...&offset=0`:
  cached providers, or models for an exact provider; 50 results per page.
- `GET/PATCH /api/v1/provider-connections/{id}/models/{offer_id}/model-reference/`
- `GET/PATCH /api/v1/deployments/{id}/model-reference/`

PATCH requires `mapping` and `expected_mapping` (both an exact
`{"provider":"...","model":"..."}` pair or `null`). `null` restores the default.
Stale updates return 409; reload before resubmitting. Read/edit permissions are
checked against the specific Provider/Source. Provider mappings are shared by
its associated Sources, while Source overrides never mutate another Source.

### Configure capacity and reasoning overrides

The same **Model capabilities** panel displays effective capacity, reasoning
support, selectable effort levels, the advertised default, and source/freshness
information. **Refresh from upstream** on a Provider model reads its model
directory and optional LiteLLM model-info endpoint. It does not run inference
probes or change health. An unsuccessful refresh preserves previous observations.
Source panels can reload the latest inherited observations; only Provider editors
can initiate the shared upstream refresh.

**Override model capabilities** enables per-field overrides. Blank fields inherit;
explicit `false`, `[]`, and the effort string `none` are distinct from unknown.
Provider overrides are inherited by associated Sources. A Source can override
its own fields without changing the Provider, another Source, or the shared
Canonical Model. **Restore automatic discovery / Restore inheritance** clears
only the local override. Priority is Source override, Provider override,
administrator declaration, upstream observation, exact models.dev reference,
then unknown. Known upstream capacity limits still clamp manual token limits.
Manual declarations carry their own provenance and are never marked as upstream
confirmed. Router lists remain conservative intersections across candidates.

Console endpoints use session authentication and resource read/edit permissions:

- `GET/PATCH /api/v1/provider-connections/{id}/models/{offer_id}/model-capabilities/`
- `POST` to that Provider endpoint with `{}` refreshes upstream metadata only.
- `GET/PATCH /api/v1/deployments/{id}/model-capabilities/`

GET returns `override`, effective `model_info`, `inherited_model_info`, per-field
`fields` / `inherited_fields`, and `can_edit`. PATCH replaces the local override
and requires `expected_override` for optimistic concurrency; stale saves return
409. For example:

```json
{
  "override": {
    "supports_reasoning": true,
    "reasoning_effort_levels": ["none", "low", "high"],
    "default_reasoning_effort": "low"
  },
  "expected_override": null
}
```

Supported override fields also include `context_window`, `max_input_tokens`,
`max_output_tokens`, and `supported_openai_params`. Setting `override` to null
restores inheritance. Sparse LiteLLM effort flags are discovery outputs; an
explicit configured effort list keeps them consistent. Invalid types and
contradictory defaults/lists return 400. Capacity and reasoning are available
to authorized clients together through the existing Gateway `/model/info` API.

Defaults here describe model capabilities; they do not inject an inference
parameter. Chat Completions forwards an explicitly supplied `reasoning_effort`;
Responses forwards `reasoning.effort`, including streaming requests. Omission
keeps the upstream service default. `none` and future short identifier values
are preserved exactly; Cloud does not translate `none` to `off`, clamp to `low`,
or silently raise a caller's reasoning budget. Upstream remains responsible for
accepting its advertised API values. The standard default field's narrower
LiteLLM type and extended default representation are described below.

## Snapshot operations

Both hosts schedule `apps.gateway.tasks.refresh_models_dev` every six hours.
The task validates a bounded public response before atomically replacing the
snapshot; failures keep the previous file. No credentials are sent to models.dev.
Request handling reads the local snapshot and never downloads external metadata.
Use this to bootstrap immediately:

```sh
python -m django refresh_model_catalog --settings=nexus_personal.settings
# Hosted Enterprise deployment: python manage.py refresh_model_catalog
```

`NEXUS_MODELS_DEV_CACHE_PATH` defaults to
`<BASE_DIR>/storage/model-info/models-dev.json`. Web and worker containers must
share that persistent path. `NEXUS_PROVIDER_MODEL_INFO_ENABLED=0` disables the
optional upstream `/model/info` read while retaining `/models` metadata capture.
Provider catalog refresh supplies upstream data; bootstrap does not invoke paid
inference or force all Provider health probes.

## Desktop

### Reasoning discovery

Capacity and reasoning capabilities share the same authenticated LiteLLM v1
model-info response. In addition to capacity, `model_info` contains:

```json
{
  "supports_reasoning": true,
  "reasoning_effort_levels": ["none", "low", "high"],
  "default_reasoning_effort": "low",
  "supported_openai_params": ["messages", "reasoning_effort"],
  "supports_none_reasoning_effort": true,
  "supports_xhigh_reasoning_effort": null
}
```

These names follow the upstream [LiteLLM model-info types](https://github.com/BerriAI/litellm/blob/main/litellm/types/utils.py).
The existing `model_info.reasoning` extension remains available with
`supported`, `efforts`, `default_effort` and `included_in_output`. Per-field
provenance is in `nexus.fields.reasoning_supported`, `reasoning_efforts`,
`reasoning_default`, `supported_openai_params` and the reported flags. Each
includes `value`, `sources`, `updated_at`, `stale` and `upstream_confirmed`.

Unknown metadata is `null`. A reasoning-support boolean alone does **not**
imply a selectable effort range. Explicit effort lists are preserved; sparse
LiteLLM `supports_*_reasoning_effort` flags supply only levels explicitly
reported as supported, without inventing low/medium/high defaults. `none` is
preserved as an API value and is not silently renamed to a client's `off`.
Future upstream levels are preserved as safe short identifier strings; a client
must check its own runtime support before presenting them as usable choices.
LiteLLM's standard `default_reasoning_effort` currently allows only none,
minimal, low, medium, high and xhigh. Other explicit defaults remain available
in the `reasoning.default_effort` extension and `nexus.fields.reasoning_default`;
the narrower standard field returns null instead of an incompatible value.
An explicit unsupported flag, or a parameter list excluding both `reasoning`
and `reasoning_effort`, suppresses selectable levels. The model may still reason
at a service-controlled setting when it does not expose an effort parameter.

Provider refresh reads optional model-info even when capacity fields are already
complete. Explicit reasoning declarations from `/model/info` take precedence
over `/models`; capacity retains its existing conservative behavior. For an
endpoint ending in `/v1`, a 404/405 permits trying the same-origin `/model/info`
path within the existing five-second discovery budget. Authentication failures
and redirects never trigger a second destination, and the response remains
bounded to 2 MiB. Discovery does not make inference calls or alter model health.

Explicit administrator overrides retain priority, then upstream observations,
then an exact Provider/model match from the cached models.dev reference. The
reference is not upstream verification. Neither source infers capabilities from
the model's display name. Changing the runtime endpoint invalidates its old
observations. Routers expose the intersection of confirmed effort/parameter
sets across all eligible candidates; any unknown candidate leaves that field
unknown. Disjoint known lists produce an empty list. A default is exposed only
when candidates agree and it belongs to their shared effort list.

Desktop reads `/model/info` first, falling back to `/models` when unavailable.
It displays metadata sources and stale-cache status. Automatic mode applies a
recommended output budget no greater than 32,768, the output maximum and half
the local context window. It keeps independent input limits for Pi compaction.
Manual mode and existing budgets remain available. Metadata lookup does not
save the connection or silently change active conversations.

## Reproducible third-party acceptance

The opt-in live-server test uses **installed** `inspect-ai==0.3.287` and
`litellm==1.108.0`, not copied client parsers. It validates the LiteLLM deployment
schema, Inspect's HTTP discovery and full provider construction with capacity
read from Nexus. It uses a disposable scoped Router credential.

Install those packages in an isolated virtual environment, set
`NEXUS_MODEL_INFO_CLIENT_PYTHON` to its Python executable, and run:

```sh
python -m django test nexus_personal.tests.test_model_info nexus_personal.tests.test_model_info_client --settings=nexus_personal.tests.settings
```

`tests/model_info_third_party_client.py` also accepts a JSON configuration on
stdin (`base_url`, `api_key`, `model`, optional `headers` and `generate`). Keep
credentials out of arguments, logs and source control. `generate: true` performs
one bounded inference through the real Inspect provider; discovery alone never
invokes a model. Models with genuinely unknown capacity may require Inspect's
explicit `require_model_info=False`; Nexus never fabricates a limit to pass it.

### Acceptance recorded on 2026-11-04

- Hosted regression suite: 12 tests passed; exported Community suite: 10 passed,
  including the real HTTP/client test above.
- Public Cloud discovery and all path aliases returned 200. A temporary,
  model-and-Router-scoped key exposed only its authorized model. Unknown IDs
  returned 400; inaccessible names returned 404; anonymous and revoked keys 401.
- Inspect's real MiniMax invocation returned the expected short completion
  and a normal stop. No maximum-context or maximum-output inference was run.
- The direct `api.minimax.cn` offer was explicitly mapped to the exact
  `minimax-cn/MiniMax-M3.1-Flash` reference. Reference capacities have
  `upstream_confirmed: false`; prices remain unknown.

Inspect 0.3.287 gives its own recognized-model database precedence over proxy
metadata. The controlled unknown-alias test verifies Nexus capacity consumption;
for recognized models, client-selected limits can differ from this endpoint.
This compatibility layer does not override third-party client policy or claim
support for LiteLLM's v2 or administrative APIs.

### Community Responses transport

The Community Responses facade forwards `reasoning.effort` as the identical
`reasoning_effort` value to its Chat Completions backend. Streaming uses the
same mapping. It does not implement Hosted native Responses transport,
reasoning summaries or opaque reasoning-history replay. No effort is injected
when it is absent from the request.
