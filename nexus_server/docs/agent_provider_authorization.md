# Caller-owned provider authorization

Agents can request a provider credential through MCP URL elicitation. The human
enters the secret on an authenticated Cloud page, never in a tool argument or chat.
This grants provider access; it does not configure an execution runner or transfer
the calling client's own login tokens.

## Deployment

Apply the host's Agent migrations, configure `NEXUS_SECRET_ENCRYPTION_KEYS`, use a
public HTTPS `NEXUS_PUBLIC_BASE_URL`, and enable selected Agent UUIDs in the
comma-separated `NEXUS_AGENT_CREDENTIAL_ENABLED_AGENTS` environment variable.
The default allowlist is empty. Restart the Web service after configuration changes.
Use the same encryption keyring in all processes that serve these callbacks.

The SDK helper is `nexus_agent.credentials.ProviderCredentials(current_run())`.
`authorize(provider, wait_seconds=15)` accepts `openai`, `anthropic`, or `nexus`.
Each provider has a fixed HTTPS endpoint; arbitrary credential destinations are
not supported. Publish a tool that returns only this helper's authorization status.

Clients advertising `elicitation.url` receive `elicitation/create` requests. Clients
without that capability receive a pending result and a login-protected URL. The
human signs in as the invoking owner, explicitly confirms access, and supplies the
provider key/token on that page. This is not a provider OAuth redirect flow.

Pending requests expire after 10 minutes. Grants last 5 minutes to 24 hours, with
a 1 hour default. Calls from the same Agent, tenant, project and caller can reuse
a valid grant. Reopening the authorization page permits revocation. Decline and
revocation clear the stored encrypted secret. Expired grants cannot be resolved.

## Trust boundary

Cloud establishes the caller identity; neither uploaded project keys nor caller
claims in tool arguments establish ownership. Shared or unidentified API keys must
be rejected. The hosted Cloud requires personal Agent credential-vault ownership
for API key callers. The Personal host uses its own verified owner identity and
restricted Agent authentication; it does not import commercial API key models.

The secure page uses session authentication, CSRF protection, no-store responses,
a restrictive CSP, and no external scripts. The URL contains a reference only.
An MCP `accept` response only acknowledges opening that URL; it cannot approve a
grant. User approval is checked server-side before the waiting invocation resumes.

`resolve_for_runtime(authorization_id)` is exclusively for trusted execution code.
The internal callback checks its token, active invocation, owner, caller scope,
grant lifetime and revocation before returning plaintext over HTTPS. Never expose
this method as an MCP tool, persist its output, include it in model prompts, or
send it to Run events, logs, artifacts or training exports. Keep credentials within
the authorized operation and discard them afterward.

Atrex's current preparation example exposes authorization but does not yet resolve
provider secrets for model calls or execute GPU work. Operators must complete and
validate execution isolation before enabling those capabilities.

## Verification

Exercise URL capability negotiation, no-capability link fallback, approval and
reuse, wrong-owner rejection, scope isolation, expired grants, revocation and
CSRF enforcement. Use disposable synthetic secrets and revoke them afterward.
Tests must also ensure repeated JSON-RPC IDs do not share Relay replay-cache keys.
SDK examples and tests are in `examples/atrex_mcp_agent` and `tests/test_credentials.py`.
