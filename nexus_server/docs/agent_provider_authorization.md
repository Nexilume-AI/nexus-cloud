# Caller-owned provider authorization

Agents request caller-owned model access through MCP form or URL elicitation.
Nexus Cloud Router/model selection needs no user-supplied key. New third-party
secrets are entered on an authenticated Cloud page, never in a tool argument or chat.
This grants provider access; it does not configure an execution runner or transfer
the calling client's own login tokens.

MCP connection authentication and upstream model authorization are separate:
the first identifies the invoking caller; the second permits scoped, potentially
billable model requests. A connected MCP client does not imply model consent.

## Deployment

Apply the host's Agent migrations, configure `NEXUS_SECRET_ENCRYPTION_KEYS`, use a
public HTTPS `NEXUS_PUBLIC_BASE_URL`, and enable selected Agent UUIDs in the
comma-separated `NEXUS_AGENT_CREDENTIAL_ENABLED_AGENTS` environment variable.
The default allowlist is empty. Restart the Web service after configuration changes.
Use the same encryption keyring in all processes that serve these callbacks.

The SDK helper is `nexus_agent.credentials.ProviderCredentials(current_run())`.
`authorize(provider, wait_seconds=15, *, base_url=None, model=None, force_new=False)` accepts
`openai`, `anthropic`, `nexus`, `custom_openai`, or `custom_anthropic`.
Presets have fixed endpoints. Custom providers require a public HTTPS API Base URL.
The approval page lets the owner select provider, endpoint, exact model, lifetime,
and either a new key or an eligible saved credential. Publish only authorization
metadata through MCP, never the resolved secret.

### Nexus Cloud: Router selection without a key

Nexus Cloud hides the endpoint/key fields and lists deployed Routers the invoking
owner can use in the calling tenant/project context, with their actual output model
names. Selection and explicit consent issue a dedicated temporary credential for
one Router/model. Both the execution broker and gateway enforce expiry/revocation;
gateway admission also rechecks membership, Router use permission, original caller
identity, and inference-only scope. Billing belongs to the selected Router context.
This issuer is available in the Enterprise/Cloud composition; Personal does not
silently import a different credential system.

Clients advertising form elicitation can choose a Nexus Router/model, lifetime
and consent in their own UI. Existing third-party credentials can be confirmed
in-band only for their already-authorized model/endpoint. New secrets and changed
third-party scopes use URL elicitation. Form replies are bound to the original
caller, Agent and MCP session; only server-offered choices can be approved. A URL
`accept` is still only navigation acknowledgement. Clients without form support
use the secure authorization page.

Use `force_new=True` to request replacement credentials/configuration while keeping
existing grants and running jobs intact. An existing job never switches credentials
mid-run. To switch that job, cancel it and submit a new job with the new grant.

```python
grant = credentials.authorize('custom_openai', base_url='https://example.com/v1',
                              model='exact-model-id', wait_seconds=0)
# After approval, read the actual scope selected by the owner:
grant = credentials.status(grant['authorization_id'])
```

The approved endpoint and model are immutable. A change requires another request
and explicit consent. Saved credentials are limited to the same caller, Agent,
tenant, project, provider and endpoint. Derived grants cannot outlive their source;
revoking the source disables all derived grants. An omitted model on the initial
request is a prompt to choose it, not permission to call arbitrary models.

Clients advertising `elicitation.url` receive `elicitation/create` requests. Clients
without that capability receive a pending result and a login-protected URL. The
human signs in as the invoking owner, explicitly confirms access, and supplies the
provider key/token on that page. This is not a provider OAuth redirect flow.

Pending requests expire after 10 minutes. Grants last 5 minutes to 24 hours, with
a 1 hour default. Calls from the same Agent, tenant, project and caller can reuse
a valid grant. Reopening the authorization page permits revocation. Decline and
revocation clear the stored encrypted secret. Expired grants cannot be resolved.

The current confirmation interfaces offer 300, 3600 or 86400 seconds. SDK
`wait_seconds` accepts 0–20 seconds (default 15) and controls only the invocation's
wait for confirmation. A pending result is not a denial: approve before the
10-minute request expiry, then query the same authorization ID. Grant reuse also
matches provider/endpoint and, when specified, exact model. If model is omitted,
read the returned model rather than assuming a new selection will be requested.

### Atrex MCP client contract

Call `authorize_atrex_provider` with `{"provider":"nexus","wait_seconds":15}`
to let the user choose an available Router/model. An optional `model` requests a
particular model, subject to availability and final user selection. The result
contains non-secret metadata including `authorization_id`, `status`, `provider`,
`base_url`, `model`, `router_id`, `expires_at` and `authorization_url`.

After confirmation, call the same tool with `{"authorization_id":"<returned-id>"}`.
Only `authorized` permits execution. Use the actual approved provider/model in
`start_atrex_optimization`, together with project credentials, the grant ID and a
fresh UUID request ID. Reuse that request ID for transport retries. To change a
Router or API configuration, make a new authorization invocation with
`force_new: true`; passing an authorization ID only queries that existing grant.

The authorization page has a flat English form with provider-specific inputs,
explicit consent, lifetime selection and a revoke action. Nexus hides endpoint
and key fields. New credentials are never collected through chat or form
elicitation. Task progress subscriptions are independent of this authorization
flow; new Atrex jobs use an authenticated SSE page and `watch_atrex_job`, while
`get_atrex_job` remains available for snapshots.

## Trust boundary

Cloud establishes the caller identity; neither uploaded project keys nor caller
claims in tool arguments establish ownership. Shared or unidentified API keys must
be rejected. The hosted Cloud requires personal Agent credential-vault ownership
for API key callers. The Personal host uses its own verified owner identity and
restricted Agent authentication; it does not import commercial API key models.

The secure page uses session authentication, CSRF protection, no-store responses,
a restrictive nonce CSP, and no external script dependencies in its template.
The URL contains a reference only.
An MCP `accept` response only acknowledges opening that URL; it cannot approve a
grant. User approval is checked server-side before the waiting invocation resumes.

`resolve_for_runtime(authorization_id)` is exclusively for trusted execution code.
The internal callback checks its token, active invocation, owner, caller scope,
grant lifetime and revocation before returning plaintext over HTTPS. Never expose
this method as an MCP tool, persist its output, include it in model prompts, or
send it to Run events, logs, artifacts or training exports. Keep credentials within
the authorized operation and discard them afterward.

The Atrex execution broker resolves credentials only in trusted host memory. It
checks the approved endpoint and exact model, resolves and pins public IPs on each
model connection, verifies TLS against the original hostname, and refuses redirects.
Workers have no network and receive only a scoped local proxy capability. Custom
OpenAI-compatible endpoints must implement Responses for the Codex runner; custom
Anthropic-compatible endpoints must implement Messages for the Claude runner.

## Verification

Exercise URL capability negotiation, no-capability link fallback, approval and
reuse, wrong-owner rejection, scope isolation, expired grants, revocation and
CSRF enforcement. Use disposable synthetic secrets and revoke them afterward.
Tests must also ensure repeated JSON-RPC IDs do not share Relay replay-cache keys.
SDK examples and tests are in `examples/atrex_mcp_agent` and `tests/test_credentials.py`.


## Personal distribution boundary

This open-source host supports owner-bound secure credential entry, custom public HTTPS endpoints, saved grant confirmation, and background task events. Hosted Nexus Router credential issuance is not included: the Personal page uses a caller-supplied Nexus key or another provider. It never falls back to an installation credential. The Atrex execution example currently pins its authorization and telemetry callbacks to hosted `https://cloud.nexilume.com`; using a different Cloud host requires explicitly adapting and reviewing both callback allowlists.


## Provider selection in the authorization page

**Model provider** is the single entry point for choosing a connection; there is
no separate “Use my Nexus Router” button. The page uses a compact card layout,
responsive inputs, and an explicit consent checkbox, without expandable sections.

The Personal host uses caller-supplied credentials, including when Nexus Cloud is
selected. It does not expose the hosted `/account/` or short-lived `/link/` flow.
Router controls appear only when the backend explicitly advertises Router
delegation support. Switching providers clears the key and confirmation; the
selector is disabled while an approval is being submitted.

On hosted **cloud.nexilume.com**, choosing **Nexus Cloud** in **Model provider**
requests a real browser login and lists only that signed-in user's permitted
Routers, with their billing Organization/project. A pending authorization link
does not grant access to the publisher's account. Other providers can be
configured without login using a valid five-minute, one-time authorization link;
the user still supplies their own key and explicitly confirms. Approval or
cancellation consumes the link. These hosted capabilities are separate from the
Personal host's credential-entry flow.


## Optional preauthorization in MCP client configuration

Both the hosted service and Personal host accept explicit upstream configuration
on Agent MCP requests. This is a Nexus HTTP-header extension, not a standard MCP
tool argument. Existing web authorization remains available when these headers
are omitted. Use a personal, owner-bound MCP connection credential and an Agent
enabled for model authorization by the host administrator.

```toml
# Optional upstream model preauthorization for this MCP Agent.
# Environment variables contain keys; never put them in tool arguments.
[mcp_servers.my_agent]
url = "https://cloud.nexilume.com/api/v1/agents/<agent-id>/mcp/"
bearer_token_env_var = "NEXUS_MCP_API_KEY"

[mcp_servers.my_agent.http_headers]
X-Nexus-Tenant = "<tenant-id>"
X-Nexus-Upstream-Preauthorize = "true"
X-Nexus-Upstream-Provider = "custom_openai"
X-Nexus-Upstream-Endpoint = "https://api.example.com/v1"
X-Nexus-Upstream-Model = "<model-id>"
X-Nexus-Upstream-TTL-Seconds = "3600"

[mcp_servers.my_agent.env_http_headers]
X-Nexus-Upstream-Key = "NEXUS_UPSTREAM_API_KEY"
```

Codex supports static `http_headers` and environment-sourced `env_http_headers`
([official configuration reference](https://developers.openai.com/codex/config-reference/)).
Set `NEXUS_MCP_API_KEY` and `NEXUS_UPSTREAM_API_KEY` in the environment of the
process running the MCP client. The first authenticates to Nexus; the second is
the upstream model credential. They serve different purposes. Other HTTP MCP
clients can send the same headers. A client without environment-backed headers
can supply `X-Nexus-Upstream-Key` directly in its private header configuration;
do not commit or share a configuration containing a real key. Arbitrary top-level
`endpoint`, `model`, or `key` fields are not read by the Nexus MCP endpoint.

| Header | Requirement |
| --- | --- |
| `X-Nexus-Upstream-Preauthorize` | Must be the string `true`; explicitly permits this Agent to use the supplied model credential and incur model charges. |
| `X-Nexus-Upstream-Endpoint` | Required public HTTPS API base; no credentials, query, fragment, or private DNS/IP destinations. |
| `X-Nexus-Upstream-Model` | Required exact model identifier. |
| `X-Nexus-Upstream-Key` | Required upstream API key, separate from MCP `Authorization`. |
| `X-Nexus-Upstream-Provider` | Optional; defaults to `custom_openai`. Also supports `custom_anthropic`, `openai`, `anthropic`, and `nexus`. Preset providers require their fixed endpoint. |
| `X-Nexus-Upstream-TTL-Seconds` | Optional; defaults to `3600`, allowed `300`–`86400`. |

Incomplete configuration, missing explicit consent, unknown upstream headers,
and invalid endpoints fail closed with HTTP 400 rather than silently enabling
access. `Preauthorize: false` alone is equivalent to no configuration; remove
all other upstream headers when disabling it.

The gateway strips these headers before forwarding or queueing a request. It
creates an encrypted, caller/Agent/tenant/project-scoped grant when a tool
invocation is admitted. Initialization and discovery do not create grants.
Each new invocation carrying the complete explicit configuration creates a new
grant with the selected lifetime; this is standing client consent, not an
additional web confirmation on each call. A running job keeps its original
authorization and cannot extend it by polling. Removing configuration prevents
new invocations from using a previous configured grant, but does not cancel
already running jobs; revoke the grant or cancel the job to stop existing use.

`ProviderCredentials.authorize()` returns `authorized` with `preauthorized: true`
and the selected non-secret configuration. The configured provider, endpoint,
and model take priority over tool defaults, including `force_new`. Always use
the returned configuration to start work. Changing any of these values or the
key requires obtaining a fresh authorization reference; old references fail
against a different invocation configuration. No configured grant is offered as
a saved web credential. Calls without configuration continue the existing web
approval flow. To return to web approval, remove the upstream headers.

Keys are kept out of MCP arguments/results, Agent-forwarded headers, Run events,
and training exports. Only the trusted execution callback resolves the encrypted
credential after scope and lifetime checks. Reverse proxies and operator-added
HTTP instrumentation must also exclude `X-Nexus-Upstream-Key` from header logs.
