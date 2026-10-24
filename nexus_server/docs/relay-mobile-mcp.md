# Mobile Agents over Relay and MCP

Mobile support is a capability contract, not an Agent-name or Agent-ID allowlist.
An Agent declares `mobile_requirement` and `mobile_capabilities`; tools may narrow
those scopes with `mobile_scopes`. The registered Relay node must advertise true
`relay`, `runtime_context_v1`, `invoke_interactions_v1`, `mcp_stream_v1` and
`caller_mobile_v1` capabilities, plus `mcp_tasks_v1` for task tools. Old connectors
that do not advertise this contract must be upgraded. Mobile no longer uses
`NEXUS_AGENT_RELAY_MOBILE_AGENTS` or depends on the Chat rollout allowlist.

## Caller flow

1. Pair a phone with Nexus and select it as the Agent's caller-owned Mobile binding.
2. Invoke the Agent's tool through authenticated MCP. Use a task-capable client for
   long-running tasks. Invocation authorizes this Run's declared Mobile scopes:
   there is no separate Cloud Mobile grant dialog or per-command approval roundtrip.
3. Follow progress via the Agent's Interactive View, and cancel via MCP Tasks or
   its console. Model-provider authorization is a separate, unchanged workflow.

The server records invocation consent on the Run; tool arguments and HTTP headers
cannot set this flag. It is never inferred for anonymous requests or historical
Runs. A manually restricted or revoked Mobile grant still takes precedence.
Revoking Mobile access creates a denial even when there was no previous grant.

This consent applies only to the selected device owned by the authenticated caller
in the selected organization/project. A shared Nexus API key is one caller.
The personal MCP key exported from an Agent's API access page can use that key
owner's existing Mobile binding for the same Agent and organization/project.
Cloud verifies this relationship from its credential vault on every delegate
request. Arbitrary keys do not inherit their issuer's phone, and external-user
identities cannot use this mapping. Sharing a personal Agent key shares its
Mobile access; use distinct personal keys for distinct users. Run/workspace and
model authorization identities remain API-key scoped. Either the owner's or
the key's Mobile grant restrictions apply, and revoking the owner's Mobile access
also cancels pending commands and releases device leases for these key Runs.
Binding deletion, device disablement, expiry, scope reduction, cancellation and
exclusive device leases remain enforced. Android accessibility/screen sharing
permissions remain required. Direct device MCP and Private Display retain their
existing approval policies; no global device approval mode is changed.
