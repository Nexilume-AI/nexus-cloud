# OpenAI-compatible prompt caching

Nexus forwards cache controls to the selected upstream. It does not cache model
answers or share generated replies between callers. A cache key is a routing hint,
not an authorization credential. Router permissions, model limits, admission,
usage attribution and the original caller still apply at every cascade boundary.

## Requests

Both `POST /api/v1/openai/v1/chat/completions` and
`POST /api/v1/openai/v1/responses` accept:

| Field | Accepted values |
| --- | --- |
| `prompt_cache_key` | Opaque string; whitespace is retained |
| `prompt_cache_retention` | `in_memory`, `24h` (older upstream contracts) |
| `prompt_cache_options` | Object with optional `mode` (`implicit`, `explicit`), `ttl` (`30m`, `12h`), `prewarm` (boolean) |
| `prompt_cache_breakpoint` | On a text/image content part: `mode: explicit`, optional `ttl: 30m` or `12h` |

Use **either** `prompt_cache_retention` **or** `prompt_cache_options`. Availability
is model- and upstream-specific; an OpenAI-compatible endpoint need not implement
all OpenAI cache features. Unsupported controls remain an upstream error. Nexus
never removes them and silently repeats a rejected inference request.

Example Chat request body (requires an upstream implementing explicit caching):

```json
{
  "model": "your-router-model",
  "prompt_cache_key": "project-spec-v1",
  "prompt_cache_options": {"mode": "explicit", "ttl": "30m"},
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "Your stable long prefix...",
       "prompt_cache_breakpoint": {"mode": "explicit", "ttl": "30m"}},
      {"type": "text", "text": "Your current question"}
    ]
  }]
}
```

For Responses, use native `input` items and `input_text` / `input_image` content.
The Community Responses route now uses the existing native transport, preserving
opaque items, cache controls, output and completed-event usage instead of
translating the request through Chat. This transport is stateless: `store: false`
is supported; server-side conversation storage is not implemented.

## Usage and pricing

Upstream JSON and SSE usage details are preserved. Chat uses
`usage.prompt_tokens_details`; Responses uses `usage.input_tokens_details`:

```json
{
  "cached_tokens": 4000,
  "cache_write_tokens": 7000,
  "cache_write_tokens_details": {"ttl_30m_tokens": 3000, "ttl_12h_tokens": 4000}
}
```

For a 12,000-input-token request, these counts leave **1,000 ordinary input
tokens**, not 8,000. Writes replace the ordinary input tariff for their tokens;
they are not an extra charge on top of the input tariff. Counts must not overlap.

The Model Offer update API
`PATCH /api/v1/provider-connections/{account_id}/models/{offer_id}/` accepts
nullable nonnegative cache-write prices, in the same currency and per-1,000-token
units as existing input/output prices:

- `cached_input_price_per_1k_tokens`: cache reads (existing field).
- `cache_write_price_per_1k_tokens`: writes with no reported TTL split.
- `cache_write_30m_price_per_1k_tokens`: reported 30-minute writes.
- `cache_write_12h_price_per_1k_tokens`: reported 12-hour writes.

An unset TTL price falls back to the general write price, then to the configured
input price. An unset read price falls back to input. Existing flat Source pricing
remains the fallback if only some differentiated prices are configured. There is
no automatic import of a vendor's public price or assumed write multiplier.

Hosted settlement applies these disjoint categories once at the final Source,
including Provider Marketplace revenue, exact rounding carry and the consumer's
single Gateway charge. Intermediate aggregate Routers do not bill again. An
admission estimate uses the largest configured token price, without assuming a
future cache hit. Community records usage and configured price estimates but
continues to have **no platform wallet charge**.

Authorized Router traces at `/api/v1/routers/{router_id}/traces/` include
`cache_usage`, with `reported`, `cached_tokens`, `cache_write_tokens`, TTL counts,
`uncached_input_tokens`, and `adjusted`. When a tariff is available, the receipt
also snapshots `configured_prices` and `configured_raw_amount`. Hosted `cost`
remains the final charged amount; it may differ from raw usage cost because of
rounding carry. Old receipts retain an empty cache record.

Absent usage is not a cache hit. Negative, invalid, or overlapping cache counts
are bounded to the reported input total and marked `adjusted`; raw upstream usage
remains available in the API response. Prompt text and cache keys are not stored
in the cache accounting receipt.

## Cascades and Relay

Only the logical model name is resolved to a physical model. Cache controls and
content breakpoints survive all Router levels. The outbound Model Relay transports
request and response bytes unchanged for both JSON and SSE; it does not parse away
cache options or usage. Transport reconnection does not replay POST requests.

`auto` may select a different model/Source and upstream load balancing may select
a different cache worker. Identical prefixes therefore do not guarantee a hit.
Use a stable explicit model when measuring caching, and retain a stable prefix
and cache key. This change does not promise cross-model or cross-provider cache
reuse.

## Validation and release status (2026-11-19)

- Four-level authenticated HTTP fixture: Chat and native Responses, JSON and SSE,
  controls/breakpoints preserved, one receipt per request.
- Real HTTP/WebSocket Relay fixture: request and response bytes preserved,
  including segmented SSE and a long prefix.
- Hosted ledger tests: mixed TTL/read/write pricing, Source fallback, one cascade
  debit, Marketplace revenue and historical micro-amount rounding regressions.
- Real public Responses requests: 27,830 input tokens; cold read count 0, followed
  by two requests reporting 27,648 cached tokens (99.35%). The final call streamed.
  Observed durations: 12.856, 8.578 and 6.734 seconds. These three samples are
  compatibility evidence, not a latency benchmark.
- The selected Source was configured at zero price; all three live audit records
  correctly report zero charge. This does **not** demonstrate positive monetary
  savings. Nonzero tariffs are verified in isolated ledger tests.
- The tested upstream rejected explicit `prompt_cache_options` with HTTP 400.
  It reported zero writes for implicit caching. Positive real write usage is
  **not verified**; mixed-TTL write accounting is covered by controlled fixtures.

Production deployment is intentionally pending at the owner's request. No live
Cloud code, schema, pricing, Key permissions or service restart was changed for
this release. Existing production behavior was used only for the public cache
hit checks. New public request-field acceptance and new persisted receipts still
need a post-deployment smoke test. When deployment is approved, run the additive
`gateway` and `providers` migrations before loading the new Web/Worker code.

Reference: [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching).
