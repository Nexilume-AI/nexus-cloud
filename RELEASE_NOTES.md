# Nexus Cloud Community source release notes

## 2026-09-23 repository update: runtime recovery

- Hosted Docker Agents now detect changes to the Cloud CA and HTTPS origin and
  reconcile a new container generation. Healthy replacements preserve runtime
  identity; failures retain the prior container with bounded retry backoff.
  Existing tasks drain before retirement. No business invocation is replayed,
  and certificate and hostname verification remain enabled.
- Browser delegate errors preserve recoverable unavailable/stale-observation
  codes without exposing raw Runtime errors.
- Direct API Provider settings can be saved while the upstream is unavailable;
  refresh models to verify the new connection. Old probe results are invalidated
  and stopped Providers stay stopped. Inconclusive probes retain previously
  verified models as degraded, not falsely healthy.
- Optional exact-host DNS recovery and model-probe allowlists remain disabled
  unless configured by the operator. Private-address and TLS checks still apply.
- OpenAI-compatible requests retain tool-call messages and bounded reasoning
  options. Database outages return a sanitized retryable 503.

This is a selected-source repository update, not a new full export archive.
`community-source-manifest.json` retains the initial export provenance; Git
history and `nexus_server/docs/updates/2026-09-23-runtime-recovery.md` describe
later changes. Community-specific package configuration, translations and
terminal focus changes are preserved. Commercial implementations and research
artifacts are not included. No SDK or OpenWrt protocol upgrade is required.

Validation: 66 isolated Community Server tests, 4 Web regression tests,
Community TypeScript check and production build passed. The build retains its
existing large-chunk advisory. A source secret scan found only the previously
reviewed CanonicalModel test fixture, not a working credential. No new live
Community Docker acceptance or full-release audit is claimed by this update.

## Included

This source snapshot provides the single-owner Cloud host and Community Web
Console, including Data Assets, model routing and Cloud-side edge/device APIs.
OpenWrt, Mobile, and Python SDK / Computer Runtime source and artifacts are
excluded and will be published independently. Those integrations require the
corresponding separately installed client or device release.
Commercial application composition remains separate; opening this source
archive does not open the mixed development repository or its history.

The initial Server package retains its development version, `0.0.0.dev0`.
This source release does not claim a published PyPI package, signed binaries,
automatic upgrades, or production readiness of an unconfigured installation.

## Validation basis

The shared-code separation was exercised by the complete ordinary Enterprise
regression (2,135 cases, 35 explicitly skipped cases), Community host isolation,
real installed SDK/ASGI/Computer workloads, real Provider Docker and Python
Agent Docker workloads, and independent Web/documentation builds.

The final Web Push/Provider repair has 61 passing focused regression cases.
Its only Server changes are three runtime files and two new test files.
Unchanged sources and build inputs reuse the preceding acceptance evidence;
the release does not require consumers to repeat internal full-suite runs.
Skipped live scenarios are not represented as passed.

## Security changes and deployment requirements

- Web Push uses DNS-pinned public HTTPS, does not follow redirects and does not
  inherit Provider private-network exceptions.
- Provider text/model responses and individual SSE lines are bounded. Upstream
  error bodies are not copied to user-visible errors.
- Hosted-image admission requires an administrator-managed signing key and
  exact image identity. No test signing key is a production trust root.
- Nexus Cloud credentials and certificate material are not copied into a
  Community installation. HTTPS/WSS and device scope validation remain enabled.

## Known limits

- Infrastructure, reverse-proxy TLS/WSS and process supervision require operator
  setup. There is no unattended all-in-one infrastructure installer.
- Interrupted database migrations require operator recovery; initialization
  must not silently adopt an existing unrelated database.
- Hosted execution requires Docker, configured controllers, image admission,
  and verified network isolation. Enabling a UI feature does not supply these.
- Edge operation requires installation-local enrollment/TLS material. A Relay
  service is not automatically provisioned by the Community host.
- Custom executable policies require a trusted installation operator and
  deployment resource limits; this snapshot is not a zero-vulnerability or
  general hostile-code sandbox certification.
- Platform and device claims are limited to the environments actually verified;
  do not infer real macOS acceptance from portable source or mocked tests.

## Licenses and attribution

Nexus Cloud source is Apache-2.0. Keep the complete root license and component
notices. Web builds generate bundled dependency notices, including PDF font
attribution. Independently released OpenWrt, Mobile and SDK packages retain
their own license files and upstream notices; this archive does not relicense
or distribute those components.

## Unreleased: bundled Relay startup

Windows, Linux and Compose launchers now include a separately configured Relay
runtime within the Community Server distribution. First start provisions
installation-owned credentials; restarts preserve them. The tunnel is local-only
by default. See README for first-start advertised IP and Docker bind settings.
Verified locally: independent Docker build/start, Cloud Relay availability, TLS
client-certificate and JWT rejection, credential reuse, and Relay restart.
External router pairing and public ingress require the documented Edge mTLS and
network setup. This working-tree change is not a new published source archive;
community-source-manifest.json still describes the previous exported release.
