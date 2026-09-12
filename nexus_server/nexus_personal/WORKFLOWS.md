# Community workflows and protocol guide

This guide describes the Personal composition of shared Nexus functionality.
The host is still under release integration; this document is not a claim of
completed installation, hardware acceptance or public release approval. Begin
with the [operator guide](HOST.md) for protected configuration, dependencies,
controllers, workers, network isolation and current readiness requirements.

Personal is a single-owner installation. It does not provide organization role
administration, a wallet, commercial plans or Marketplace publication. This does
not remove authentication, CSRF, scoped credentials, device consent, file-path
checks or Run isolation. Never expose a test-only unauthenticated runner.

## Providers and model routing

1. Connect your own Provider through the Personal console. A Direct API needs
   its upstream credentials; a supported login proxy needs explicit sign-in.
   Keep credentials out of source, images, command arguments and diagnostics.
2. Start the configured Provider runtime and inspect discovered models. Discovery
   and health are separate: a failed refresh must not erase the last verified
   catalog, and cached models do not prove the upstream is currently callable.
3. Add a usable model as your own Source, then configure its Model Pool. Source
   priority, fallback, health and upstream cost are operational routing inputs.
4. Configure an Execution Router against the intended Pools. An Aggregation
   Router exposes distinct Execution Routers under distinct downstream model
   names; it is not an extra same-name fallback layer.
5. Deploy and use Router SDK setup to obtain this installation's endpoint and
   scoped credential. Keep the returned credential private and use the actual
   exported model names. Do not copy a credential from another installation.

The compatibility base is `/api/v1/openai/v1`. Query its authenticated `models`
endpoint before sending Chat, Responses or image operations. Source capability
contracts, not a model's name, determine supported operations. Image generation
is not a health probe and may incur charges at your upstream Provider. Local
cost observations do not perform a Nexus wallet transfer.

Provider restart/reconciliation cannot repair revoked upstream authorization;
sign in again when requested. A deliberate Stop remains stopped. Controller
configuration and the Provider's persistent login volume must survive recovery.
Do not share one credential volume between concurrent login proxy instances.

## Author and deploy an Agent

Use the same [Python SDK](../../nexus_openwrt/sdk/nexus-agent-sdk-python/README.md)
for OpenWrt and hosted MCP. The SDK's hosting section explains the optional
FastMCP extra and `agent.as_mcp_server()`; selecting a transport does not supply
caller credentials or provision Docker.

Register/select an image, or use the explicitly enabled Python build path, then
deploy through this installation's Agent Controller. Production image admission
uses an administrator-controlled signature over an exact image ID, never a
mutable tag or a public key supplied by the image. The operator guide describes
the trust policy and its limits. A queued deployment is not proof that the
container is running: check the lifecycle Job and runtime health.

Agent versions, image selection and local CPU/memory ceilings remain available.
An Agent version publication is not a Marketplace listing. Repository metadata
upload alone is not an executable source build. A successful unit test is not
proof of network isolation or of a live Docker/OpenWrt deployment.

## Private Display, devices and follow-up

Open the owned Agent's Private Display. Its tool contract controls whether it
accepts chat, structured input, files and images. A UI selection cannot add a
missing capability to an Agent handler.

Pair your own Computer Runtime and explicitly attach it with the requested
scopes. Runtime presence, binding and scope consent are separate requirements.
Computer Browser actions must execute on the attached device, using an isolated
session; they must not silently fall back to the Agent or Cloud host. Keep the
browser sandbox enabled. Do not use historical SSH setup or browser sandbox
disablement instructions to make a failed attachment appear successful.

Plan, Chat, Shell, Browser, Files and runtime events come from the Run protocol.
The SDK receives restricted delegate URLs/tokens through its Run Context, not
the owner's login credential. Use the configured Cloud TLS trust policy; never
disable certificate or hostname verification.

The [follow-up example](../../nexus_openwrt/sdk/nexus-agent-sdk-python/examples/router_follow_up_agent.py)
demonstrates cooperative `steer_and_queue`. Receiving an instruction is not the
same as applying it: acknowledge after application or explicitly reject it.
Queue waits for the prior turn to finalize; Steer does not start a parallel
handler. Unknown outcomes and failed turns require caller review, not automatic
replay of possible external effects. Preserve the same logical request ID for
transport retries instead of manufacturing another operation.

Cancellation and lease checks are cooperative boundaries, not proof that an
external side effect stopped. Use `ctx.raise_if_cancelled()` (or its async
equivalent) before new work. Keep secrets out of events and persistent outboxes.

## Files, images and local collections

The [bounded file example](../../nexus_openwrt/sdk/nexus-agent-sdk-python/examples/router_file_agent.py)
uses Run-scoped references, chunked transfer and hashes. The
[image example](../../nexus_openwrt/sdk/nexus-agent-sdk-python/examples/router_image_agent.py)
reads and reports protected image bytes; it demonstrates transport, not model
understanding. An Attached Computer is not required for an Agent to upload a
file from its own filesystem.

Only ready immutable outputs are eligible for collection import. Import an
output by its recorded identity, not a caller-provided arbitrary filesystem path.
Source identity, size and SHA-256 must remain intact across scan and capture.
An upload's success is not scanner approval or a copyright determination.

Shared protocol references:

- [Large file transfer and SDK recovery](../docs/agent_large_files.md).
- [Image/file inspection and generated output capture](../docs/data_asset_media.md).

These references also describe Cloud-client compatibility boundaries; their
mentions of public acquisition or publication do not enable those services in
Personal. The Personal host determines the composed endpoints and local safety
limits. Use owned collections, immutable versions and authenticated downloads.
Do not assume that a MIME label makes an unsupported binary safe or that a
browser frame is automatically an archived Agent output.

## Monitoring and verification

Inspect operational health, lifecycle Jobs, Run events, import state and Inbox
items. A responding Web page does not prove that the Agent worker, Controller or
maintenance scheduler is alive. Keep existing data visible when an independent
component fails; use its specific recovery state instead of repeatedly issuing
side-effecting requests.

The operator guide identifies the Personal process roles and verification entry
points. Tests belong in isolated, fixture-owned databases, storage and containers;
do not run historical Enterprise CLI recharge or commercial acceptance scripts
against a Personal installation. Installation tests, actual network callbacks,
Docker execution and physical Computer/Mobile acceptance prove different scopes.
No one of them substitutes for the others or authorizes publishing the mixed
Enterprise repository.
