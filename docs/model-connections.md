# Reusable model connections and batch Agent authorization

Hosted Nexus Cloud provides an account-owned Model connections page at
`/api/v1/model-connections/page/`. It is linked from Connect Agent and the model
authorization page. Existing one-time browser authorizations and explicit MCP
preauthorization remain available and are not silently migrated.

## User flow

1. Sign in to your own Nexus account. Save a named model connection. Nexus Cloud
   lists only your usable Routers across organizations and their output models;
   no endpoint or API key is needed. External OpenAI/Anthropic-compatible
   connections require an exact model, public HTTPS endpoint and your own key.
2. Set the connection lifetime (up to 30 days) and total request allowance.
3. Select one or more Agents, review their per-Agent lifetime and request
   allowance, and explicitly confirm the batch. Saving a connection alone grants
   no Agent permission. New Marketplace Agents are never automatically included.
4. Continue or retry your MCP tool. The same Agent's tools and new conversations
   reuse the selected permission without another confirmation while it is valid.
5. Revoke one Agent or the whole connection from the same page. Expired/revoked
   permissions can be explicitly approved again while the connection is active;
   old runtime tokens never revive. A revoked connection requires a new connection.

The effective expiry is the earlier of connection and permission expiry. Both
request counters apply. One admitted upstream request, including failures or
timeouts, consumes one request from each counter. A streaming request counts
once, not per chunk. Concurrent admissions serialize against both counters.
Counters never reset when a permission is edited. These are **request allowances,
not token or monetary budgets**; provider/Router billing limits still apply.

## Runtime contract

Continue using `context.credentials.authorize(...)` and the runtime resolution
callback. Consume the returned provider, model and base URL; do not hard-code
the original endpoint. A reusable connection returns a scoped Cloud proxy token
and `/api/v1/agent-model-proxy` base URL. The original upstream key remains
encrypted in Cloud and is never returned to the Agent or MCP client.

Supported proxy operations are OpenAI-compatible `chat/completions`, `responses`
and Anthropic-compatible `v1/messages`, including SSE streaming. This does not
proxy file administration, provider background jobs or arbitrary API methods.
Queries are rejected; only the selected model is allowed. Caller headers are not
forwarded upstream. Endpoint resolution is pinned, public-HTTPS-only, and rejects
redirects. Request bodies are bounded to 4 MiB and non-stream responses to 16 MiB.

An explicit endpoint/model request must match the saved connection. Provider
defaults without an explicit endpoint/model may use an approved connection with
the same wire protocol. Explicit per-invocation MCP preauthorization takes
priority. Multiple approved connections for one Agent use the most recently
created matching permission ahead of independently saved one-time grants. A valid token for that permission is reused within the same invocation. Revoke
the old permission to switch an Agent deterministically to a different connection.

## Isolation and revocation

- Connections belong to the server-verified caller owner, not the publisher or
  whoever knows an authorization URL. Management requires an authenticated
  account session with CSRF protection.
- Personal Agent keys resolve through the server's credential-vault binding.
  The same key is the same caller. Sharing that key shares its caller identity;
  arbitrary shared keys without an owner binding cannot authorize a connection.
- Batch permission is for an Agent, including all its tools and future versions.
  It does not grant mobile/computer access or other platform permissions.
- Each model request validates owner, Agent, grant, permission, expiry, selected
  model and quota. Nexus Router availability is rechecked at admission.
- Revocation blocks new requests. Active response streams recheck state on each
  upstream chunk and close on revocation/expiry. Already dispatched upstream work
  cannot be undone and can still incur charges. No automatic request replay occurs.
- The original one-time grants remain separate; revoking a connection does not
  revoke independently issued older grants. Revoke those on their original pages.

## Management API

All management endpoints use the signed-in browser session, no-store responses
and CSRF for writes; they do not accept a capability link as account access.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/model-connections/` | Own connections, available Agents and own usable Routers |
| POST | `/api/v1/model-connections/` | Create with `consent: true`, name, provider, model, lifetime and allowance |
| POST | `/api/v1/model-connections/{id}/permissions/` | Explicit batch: `agent_ids`, `ttl_seconds`, `max_requests`, `consent: true` |
| POST | `/api/v1/model-connections/{id}/revoke/` | `confirm: true`; optional `permission_id` limits revocation to one Agent |

Creation requires the `account_id` returned by GET to reject account changes
between review and confirmation. It accepts `router_id` for Nexus; external providers accept `secret` and
custom providers also require `base_url`. `ttl_seconds` is 300–2,592,000 and
`max_requests` is 1–1,000,000. Defaults: one day / 500 total requests, one day /
100 requests per Agent. A batch contains 1–100 distinct available enabled Agents.
At most 32 non-revoked connections may be stored per account.

## Deployment

Migration `agents.0073_model_connections` is additive and follows the existing
interactive-view migrations. Apply it before restarting web workers. Keep the
old source and web assets for rollback; do not drop connection/grant data during
an incident. Regression tests include a PostgreSQL four-way admission race,
migration reverse/forward round trip, owner separation, caller-key binding,
explicit confirmation, cross-Agent scope, streaming cleanup and secret redaction.
