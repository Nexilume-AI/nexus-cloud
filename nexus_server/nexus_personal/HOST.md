# Community production host: operator guide

For an end-to-end Linux native development workflow and the Linux process
launcher, see [LINUX.md](LINUX.md). Windows uses `start-nexus-community.ps1`.

The Community source distribution is a separate single-owner host. It does not
provision infrastructure automatically. Use its own configuration, dependencies
and database; never substitute Enterprise settings or test settings. Enterprise
startup is unchanged. Do not publish the mixed development tree.

For the feature flow and shared SDK references, see
[Community workflows](WORKFLOWS.md). This guide describes operator setup, not a
managed service. Source-release approval does not imply that your installation's
network, devices or container workloads are ready.

## Independent Server wheel

The source export contains a standalone `nexus_server/pyproject.toml`. After
installing the matching build/runtime dependency locks below, build from the
export's `nexus_server/` directory:

```text
python -m pip wheel --no-deps --no-build-isolation . --wheel-dir dist
```

The distribution is `nexus-community-server` `0.0.0.dev0`, targeting CPython
3.14. It packages shared runtime modules and the Personal host, not private
distributions, test hosts or commercial migration history. The source export is
independent of the private release/audit toolchain.

The installed entrypoints are `nexus-personal-install`, `nexus-personal-manage`
and `nexus-personal-process`. They preserve protected configuration and owner
validation. The wheel does not provision PostgreSQL/Redis/TLS, bundle the compiled
Console, supervise services or make an unavailable device reachable.

On Windows, the source distribution also includes the official process launcher
`start-nexus-community.ps1`. It requires an already prepared and initialized
installation, applies or checks only the Personal migration graph, and starts
the Community web/worker/beat processes plus explicitly configured local
controllers. It never provisions PostgreSQL, Redis or TLS and never selects the
Enterprise host, Vite development server, OpenWrt Relay or a shared database.
The tracked `-Restart` and `-StopOnly` operations are scoped to that installation.
An explicit `-LocalHttp` is available only when the protected configured origin
uses the same loopback port. It disables HTTPS-only browser behavior for that
loopback test process, never changes the stored configuration and never permits
a non-loopback listener. Do not use it for a production or shared installation.

Build the Console using `npm run build:community` in `nexus_web/`, then supply
the whole verified `dist/community/` directory to installation preparation.
Retain the asset manifest and upstream notices.

## Isolated Python dependencies (integration only)

`requirements.in` and `build-requirements.in` describe Community's third-party
dependencies separately from Enterprise's unchanged `requirements.txt`.
The `*-win-py314.txt` files pin versions and SHA-256 distribution hashes for
**CPython 3.14, Windows x86_64 only**. Separate `*-linux-py314.txt` locks target
Linux x86_64. Neither set is a complete installer, a source package, a
vulnerability audit or permission to publish; macOS is not validated.
Do not install them into an existing Cloud environment. Shared-core dependencies
are retained until isolated feature coverage proves a dependency unnecessary.

For private Linux integration, run from the repository root with
`NEXUS_PERSONAL_LINUX_ACCEPTANCE=1`:

```text
python -m tools.community_release.linux_acceptance --docker <docker-executable>
```

This reuses the existing Personal host, database/request and actual SDK bridge
suites. It builds hashed dependencies against an immutable Python image, then
copies only filtered candidate sources into test-owned native Linux images.
The Cloud config and private implementation directories are physically absent.
Containers run as UID 10001, read-only, without external networking, host mounts
or a Docker socket; scratch space is bounded tmpfs. Source images are **private
test artifacts**, not reviewed packages or public exports. Exact randomly named
fixture containers and image tags are removed after verification; shared base
images, existing containers and volumes are not pruned. BuildKit may retain its
normal local build cache, which must never be treated as a publishable image.
The runner requires at least 2 GiB of Docker VM memory before staging anything;
other running containers need additional headroom. A container `--memory 2g`
limit does not allocate that memory to WSL. The runner reports insufficient VM
capacity explicitly and never changes global WSL/Docker settings itself.
PostgreSQL installation, actual built Web installation and installed external
workloads require their separate acceptance and are not covered by this runner.

From the Server source root, create a new venv without system-site-packages.
Use that environment's Python for both installation commands (shown as `<python>`):

```text
<python> -I -m pip --isolated install --index-url https://pypi.org/simple --require-hashes --only-binary :all: --no-cache-dir --disable-pip-version-check -r nexus_personal/build-requirements-win-py314.txt
<python> -I -m pip --isolated install --index-url https://pypi.org/simple --require-hashes --only-binary :all: --no-binary http-ece --no-build-isolation --use-pep517 --no-cache-dir --disable-pip-version-check -r nexus_personal/requirements-win-py314.txt
<python> -I -m pip check
```

`http-ece`, required by the existing Web Push adapter, has no usable upstream
wheel. Its hashed source distribution is the only source-build exception;
installing the build lock first and disabling build isolation prevents implicit
unlocked build-dependency installation. The initial acceptance uses the pip 25.2
bundled with Python 3.14. Its default legacy build path emitted a deprecation
warning; the standard PEP 517 build was then separately verified with the same
hashed source and locked build environment and is selected explicitly above.
Newer pip versions still need acceptance. The resulting locally built wheel's
hash is not a reproducible-build claim or an upstream signature.

Regenerate with uv 0.9.18 from the repository root, then review and retest both
locks. Never replace them with a developer-machine `pip freeze`:

```text
uv pip compile nexus_server/nexus_personal/build-requirements.in --python-version 3.14 --python-platform windows --generate-hashes --no-header --no-annotate --only-binary :all: --default-index https://pypi.org/simple --no-python-downloads --output-file nexus_server/nexus_personal/build-requirements-win-py314.txt
uv pip compile nexus_server/nexus_personal/requirements.in --python-version 3.14 --python-platform windows --generate-hashes --no-header --no-annotate --build-constraints nexus_server/nexus_personal/build-requirements-win-py314.txt --default-index https://pypi.org/simple --no-python-downloads --output-file nexus_server/nexus_personal/requirements-win-py314.txt
```

Run `python -m unittest tests.test_personal_dependencies -v` from the Server
root. Set `NEXUS_PERSONAL_DEPENDENCY_ACCEPTANCE=1` only in the new test environment
to verify exact installed versions, every requested extra and transitive
dependency, and absence of global/user-site or unexpected installed packages.
This opt-in contains no credentials and does not install anything. Also run the
existing Personal import/database/HTTP and production initialization suites with
that same interpreter; a successful resolver or `pip check` is not sufficient.
The current SDK-source test bridge is still a source-tree integration, not proof
of an independently packaged SDK/Server installation.

The real SDK MCP fixtures additionally require the optional `fastmcp` SDK extra.
This test dependency is separate from production-host requirements. Compile
`test-requirements.in` with the runtime and build locks as `--constraint` inputs
and the same platform/hash options, then install `test-requirements-win-py314.txt`
using `--require-hashes --only-binary :all:` in the test environment only. Set
`NEXUS_PERSONAL_DEPENDENCY_TEST_EXTRAS=1` for the installed-closure assertion after
that installation. Overlapping test/runtime/build pins must agree exactly;
adding a test dependency must not silently upgrade the host under test.

## Installation preparation (integration entrypoint)

`python -m nexus_personal.install prepare` now prepares a **new, dedicated**
installation directory. This is not a complete installer: PostgreSQL/Redis
provisioning, HTTPS certificates/proxy, process supervision, controller admission,
Docker egress, Edge key material and installed-workload acceptance remain separate
unverified steps. It does not start services, initialize a database or make the
mixed source repository safe to publish.

From the Server source root:

```text
python -m nexus_personal.install prepare --directory <new-absolute-directory> --origin https://<personal-host> --infrastructure-file <protected-json> --web-bundle <verified-community-build-directory>
python -m nexus_personal.install check --directory <prepared-absolute-directory>
```

The parent directory must already exist. Existing destinations, symlinks and
junctions are refused; no installation or permissions are silently adopted.
Preparation creates and verifies a current-user-only directory before writing
secrets (POSIX 0700/0600; Windows explicit user SID and inherited private child
ACLs). It generates independent instance, Django, encryption and optional
controller credentials. Inputs, credentials and paths are not printed by the
commands. `host.json` is written after verified assets; a completed preparation
has `installation.json`. The configuration path is the only value subsequently
selected through `NEXUS_PERSONAL_CONFIG`.

The explicit protected infrastructure JSON contains `schema_version: 1`,
`database` and `redis_url`, using the same fields/TLS rules below. It may contain
`controllers`, `monitoring` and `python_builder`; controller fields are the documented ones except
`token`, which preparation generates and refuses as input. It must not contain
Cloud host configuration, instance keys or other fields. There is no fallback to
an Enterprise configuration file, environment credentials or existing services.
Use a separately provisioned personal database/Redis identity, not Cloud's file.
Preparing the file does not prove those credentials work or create that database.

Only files listed in the hash-verified Community asset manifest are copied.
Rechecking reads current files, rejects modified config/assets/manifests and
checks private state-directory permissions. These receipts detect corruption and
unexpected changes, not publisher authenticity; obtain artifacts from the reviewed
release pipeline. They are not signatures or authorization to export source.

On failure, a private partial destination is retained for explicit inspection;
`installation-incomplete.json`, when present, makes `check` fail. No recursive
rollback or automatic retry can erase an existing directory. A failure before
the marker is written still has no completed receipt. Do not point processes at
a failed preparation. An existing destination requires deliberate operator
recovery or a new directory; this command is not a secret-rotation/upgrade tool.
`check` reports only `prepared` and `unverified_steps`, with database/service
health explicitly `not_checked`; it never claims a working Cloud from local files.

## Inputs and boundaries

### Initialize a prepared installation

**Integration status: not accepted for release.** Initialization is exercised
by the real PostgreSQL acceptance suite. A successful initialization does not
prove that the proxy, workers, controllers or installed workloads are ready;
verify the independent operational requirements below.

After separately provisioning a **new empty PostgreSQL database**, initialize
the prepared directory from a fresh process:

```text
python -m nexus_personal.install initialize --directory <prepared-absolute-directory> --email <owner-email>
```

The command securely prompts for the password twice. Automation may explicitly
use `--password-stdin` and a protected input pipe; do not put passwords in command
arguments or environment variables. It loads only this installation's verified
`host.json`, not existing Django/Enterprise settings. It applies the actual
Community migration graph and creates the existing single-owner context. It
does not start Cloud, controllers or workers, and does not claim service health.

The database must be independent and empty. An unrecognized nonempty database
is refused, including one initialized through the older manual commands below;
there is no implicit adoption or conversion. A database-local advisory lock
serializes initialization. The operational `nexus_personal_bootstrap` table binds
the database to the prepared instance ID and configuration fingerprint. Another
prepared instance pointing to that database is rejected. This command is not a
configuration rotation or database upgrade tool.

Repeating a completed initialization for the same owner returns `initialized`
without changing the password or creating another user. A different owner is
rejected. If the schema completed but creating the owner failed, retry may create
the owner without rerunning migrations. Owner creation and the completed stamp
commit in the same transaction.

The migration graph includes non-transactional concurrent indexes. A failure or
process interruption during migration therefore leaves a `migrating` stamp;
retry returns `INSTALL_INITIALIZATION_INCOMPLETE` instead of silently replaying
partial DDL. Operator inspection/recovery is required. Do not delete the stamp
or declare the schema ready without checking migration records and physical
indexes. Automatic interrupted-migration repair remains unimplemented.

`INSTALL_INITIALIZATION_BUSY` means another initializer holds the lock;
`INSTALL_DATABASE_NOT_EMPTY` or `INSTALL_DATABASE_IDENTITY_MISMATCH` means the
database cannot be safely adopted. `INSTALL_SCHEMA_UPGRADE_REQUIRED` needs a
separate reviewed upgrade workflow. Errors never return passwords, SQL or backend
connection strings. `install check` still reports local preparation integrity
only; initialization success is not proof that HTTPS, Redis, controllers, workers
or installed Agent/Computer workloads are ready.

`NEXUS_PERSONAL_CONFIG` must name an absolute, regular UTF-8 JSON file, at most
32 KiB. Its schema is:

| Field | Requirement |
| --- | --- |
| `schema_version` | `1` |
| `instance_id` | Stable, unique UUID for this installation |
| `public_origin` | One HTTPS origin, including non-default port when needed; no credentials, path or query |
| `state_dir` | Existing dedicated absolute directory, outside the source tree; not home or a filesystem root |
| `secret_key` | Independently generated Django secret, at least 50 characters |
| `encryption_key` | Independently generated Fernet key, backed up with the database |
| `database` | Object containing `name`, `host`, integer `port`, `user`, `password`, `sslmode` |
| `redis_url` | Dedicated Redis database 1–15; `rediss` for non-loopback connections |
| `controllers` | Optional independent `agent` and/or `provider` service configuration; see below |
| `monitoring` | Optional SMTP and operational retention configuration; see below |
| `python_builder` | Optional fixed-profile Python build configuration; disabled when omitted |

PostgreSQL database names start with `nexus_personal_`. Non-loopback PostgreSQL
requires `sslmode=verify-full`; a local PostgreSQL connection must explicitly
choose its TLS mode. No SQLite fallback exists. The host does **not** read
`DATABASE_URL`, `POSTGRES_*`, `.local/nexus-cloud/postgres.json`, or Cloud secrets.

On POSIX the configuration belongs to the process user and has mode `0600` or
stricter. On Windows its DACL permits only that user, SYSTEM and administrators;
an unavailable ACL check fails closed. The loader never prints file contents.
Keep storage, credentials and backups private to this installation. Preparation
automates host secrets and permissions; service and infrastructure provisioning
is still required before public release.

## Operational monitoring configuration

Collectors, evaluators and notification delivery use the explicit operational
Celery task module and Beat schedule (300/60/30 seconds respectively). Retention
is inspected daily and defaults to dry-run. These are actual worker entrypoints,
not an HTTP request pretending to be the owner; the fixed installation and active
owner/profile are validated again in the worker.

The optional `monitoring` object accepts only `smtp`, `retention_enabled` and
`retention_days`. Omitted SMTP stays **unconfigured**: pending messages remain
pending without attempted delivery, and monitoring explains the required setup.
It never silently uses an Enterprise email service or default localhost mailer.

An SMTP object contains exactly `host`, integer `port`, `username`, `password`,
`tls` and `from_email`. TLS is `starttls`, `tls` (implicit TLS), or `none` only
for `localhost`/`127.0.0.1`/`::1`. Non-loopback SMTP always requires standard
certificate-verified TLS. Authentication username/password are both supplied or
both empty. Keep the password only in the same protected host JSON, not in CLI
arguments, environment variables or status output. `EMAIL_*` environment values
are not an alternate configuration source. SMTP requests have a 15-second
timeout, bounded retry and stable Message-ID; server acceptance never guarantees
recipient delivery or exactly-once external effects.

Retention defaults to `retention_enabled=false` and `retention_days=90`; supported
retention is 30–3650 days. Enabling it permits bounded hard deletion of this
Project's explicitly tagged old snapshots and sent notification payloads. It
preserves unknown/foreign snapshot ownership, incidents, audit records, active
leases and unsent/failed notifications. This is not billing or ledger retention.
No existing production data is changed by merely loading this configuration.

## Python builder configuration

The protected infrastructure input and resulting `host.json` accept an optional
`python_builder` object. Omission or `{}` keeps builds disabled, with no profile
and no dependency network. Environment variables cannot enable it.

When present and nonempty, provide all four fields:

| Field | Meaning |
| --- | --- |
| `enabled` | Boolean; enable source uploads/build scheduling only after provisioning |
| `base_image` | Immutable **local image ID** `sha256:` followed by 64 lowercase hexadecimal digits; no mutable tags or registry credentials |
| `isolation_ready` | Explicit operator attestation that dedicated build-worker isolation and storage/egress quotas have been installed and tested |
| `dependency_network` | `none` for offline builds, or the name of a dedicated operator-managed filtered Docker network; `host`, `bridge`, `default`, container namespaces and command options are rejected |

Enabling also requires an Agent Controller with configured digest admission and
`egress_policy_ready=true`. Do not set either readiness flag just to bypass a
failure. Configuration validation does not install a scanner, firewall, Docker
network or storage quotas and does not certify their effectiveness. Preparation
continues to report service health as unverified.

Provision and inspect the vetted Python/Nexus runtime profile on the actual
worker before setting its local image ID. The existing builder checks Docker
and the immutable profile again; user source executes only in its bounded,
offline probe. Only the trusted dependency-download stage uses the configured
network. `none` works for agents needing no additional downloads; additional
requirements need the separately verified dependency network. Final deployment
still runs the configured image admission verifier. Neither this setting nor a
successful build replaces production provenance, vulnerability/license and
IPv4/IPv6 egress acceptance.

Keep the configuration but set `enabled=false` to pause new uploads. This does
not cancel or replay work already queued or executing. The existing worker
isolation check remains mandatory. These settings are Personal-only; Enterprise
configuration and running services are unchanged.

For an existing prepared installation that does not yet have an Agent
Controller, stop it first and use the guarded reconfiguration command. It
preserves the installation identity and database, verifies the exact local
profile digest, enables an internal Docker network (no outbound access), and
updates the protected installation/database fingerprints together:

```text
python -m nexus_personal.install enable-python-builder --directory <installation> --base-image sha256:<digest> --controller-port <free-loopback-port>
```

This convenience profile intentionally supports dependency-free uploads only.
Do not replace `none` with a general Docker network; third-party requirements
need a separately reviewed, filtered dependency network.

The official Community launcher starts the dedicated build worker whenever the
protected configuration enables it. For manual diagnostics, select this
installation's protected configuration and run:

```text
nexus-personal-manage run_agent_python_builds
```

For a bounded operational check, `run_agent_python_builds --once` processes at
most one available build and a bounded cleanup pass; it is not a dry run. Do not
use it against an installation whose queued work you are not authorized to run.

## Local Controller configuration

Omitting `controllers` preserves the earlier schema-v1 configuration: neither
service gains a default token, and both remain unavailable until configured.
Each configured service has an explicit non-privileged `port` (1024–65535) and
independently generated `token` (43–128 URL-safe characters). Tokens must differ
from each other and from the host's Django/Fernet secrets. Keep them in the same
protected host configuration file; never put them in process arguments, logs or
environment variables. Existing Cloud Controller environment values are ignored.

Both endpoints are fixed to `tcp://127.0.0.1:<port>` on every platform. There is no
configurable listen interface, remote endpoint or implicit Cloud fallback. Choose
two dedicated, distinct local ports. An occupied port fails rather than removing
another service's socket. These local authenticated channels are for trusted
Nexus host processes, not Internet/LAN ingress. Provision them on a dedicated
trusted Docker host; do not run untrusted local users alongside the controllers.

Additional required fields:

| Service | Field | Meaning |
| --- | --- | --- |
| Agent | `image_admission_command` | Existing absolute regular executable plus bounded argv; the existing runner appends the exact image digest and requires verifier success. Not shell text. |
| Agent | `egress_policy_ready` | Explicit boolean operator attestation after installing and verifying real Docker egress filtering. `false` prevents Controller startup; never set `true` merely to bypass the gate. |
| Provider | `release_dir` | Dedicated absolute operator-managed directory of verified Provider release receipts, separate from workload storage. The existing release verifier still validates each receipt/image on use. |

Admission executables cannot live under workload storage. Mount release receipts
read-only from the trusted build/release pipeline, not from an Agent or Provider
workspace. Config validation does not install a scanner, firewall or approved
image; an empty release directory fails on runtime admission rather than adopting
Cloud releases. A valid argument list is not proof that a verifier enforces the
intended security policy. Those installation/verification gates remain required.

### Administrator-signed Agent image approvals

The optional installed `nexus_personal.image_admission` verifier implements the
administrator-public-key trust model. Nothing is enabled automatically. Configure
the existing `controllers.agent.image_admission_command` as an argv list:

```json
["/absolute/venv/bin/python", "-I", "-m", "nexus_personal.image_admission",
 "--trust-config", "/absolute/operator-policy/agent-images.json",
 "--host-id", "YOUR-INSTALLATION-INSTANCE-UUID"]
```

On Windows use the absolute installed Python executable and policy path. The
worker appends the exact `sha256:<64 lowercase hex>` Docker **image ID**. This
is the config identity inspected by the runner, not a registry manifest digest
or a mutable tag. Approve the ID of the actual reviewed local image. Registry
provenance and the binding from its signed manifest to this ID must be checked
by the administrator's release pipeline before issuing approval.

The protected policy has exactly these fields:

```json
{
  "schema_version": 1,
  "host_id": "YOUR-INSTALLATION-INSTANCE-UUID",
  "public_key": "BASE64-OF-32-RAW-ED25519-PUBLIC-KEY-BYTES",
  "approvals_dir": "/absolute/operator-policy/approved-agent-images",
  "revoked_image_ids": []
}
```

Replace placeholders explicitly; they are unusable examples. `host_id` must equal
the installation `instance_id` and command `--host-id`. Keep this policy under
the service user's protected configuration: POSIX owner-only mode 0600, or Windows
ACL allowing only that user, SYSTEM and administrators. Parent directories and
Python installation must also be operator-controlled, outside any workload root.
Symbolic links and junctions in policy/approval paths are rejected. The process
uses the existing protected-config reader; it does not read Enterprise settings,
inherit a public key from the image, or download keys from a registry.

Each approval file is named `<image-id-without-sha256-prefix>.json`. Its envelope
has exactly `payload` and `signature`, both canonical base64 strings. The decoded
payload is at most 4 KiB of UTF-8 JSON, with exactly:

```json
{
  "purpose": "nexus-agent-image-admission-v1",
  "image_id": "sha256:EXACT-64-LOWERCASE-HEX-IMAGE-ID",
  "host_id": "YOUR-INSTALLATION-INSTANCE-UUID",
  "issued_at": 0,
  "expires_at": 0
}
```

Timestamps are integer Unix seconds; placeholder zero timestamps are invalid.
Approvals may last at most seven days, cannot start in the future, and fail at
the exact expiry instant. Worker clocks must be synchronized. Sign the **exact
payload bytes**, including whitespace/newlines, using Ed25519. No JSON re-encoding
occurs during verification. Duplicate keys, extra fields and non-finite JSON
values are rejected. The purpose string prevents use as another protocol's grant.

Example release-pipeline code (run on the trusted signing machine, **not** the
worker or Agent). Supply an already reviewed payload and an administrator-owned,
encrypted Ed25519 PEM private key. Never put its password in argv or environment:

```python
import base64, getpass, json
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

key = serialization.load_pem_private_key(
    Path("release-private.pem").read_bytes(),
    password=getpass.getpass("Release signing key passphrase: ").encode(),
)
if not isinstance(key, Ed25519PrivateKey):
    raise ValueError("Ed25519 release key required")
payload = Path("reviewed-image-approval.json").read_bytes()
envelope = {
    "payload": base64.b64encode(payload).decode("ascii"),
    "signature": base64.b64encode(key.sign(payload)).decode("ascii"),
}
with Path("signed-approval.json").open("x", encoding="utf-8") as output:
    json.dump(envelope, output)
```

Transfer only the public key and signed approval to the worker. Publish approval
files atomically after naming them for their reviewed image IDs, then invoke the
same verifier command manually with that ID before enabling deployment. No private
key generation, signing service or production trust key is installed by Nexus.
The real library verification behavior is documented in the
[cryptography Ed25519 reference](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/).

Every admission re-reads policy and approval without a success cache. To revoke an
image, add its exact ID to `revoked_image_ids` (at most 256), so restoring an old
valid receipt cannot re-admit it. Replacing `public_key` rejects all old-key
approvals. A missing/malformed policy, missing receipt, wrong image/host, invalid
signature or expired/revoked approval fails closed; diagnostics do not echo input.
Revocation prevents future admission; it does **not** stop already running tasks.
Stop affected workloads through the existing lifecycle operation if needed.

This is an administrator's signed deployment authorization, not a substitute for
image vulnerability/license scanning, reproducible builds, registry provenance,
egress enforcement or operator review. Tests use ephemeral keys, never production
trust. Enterprise/external verifier configurations are unchanged.

### Worker egress policy and optional lifecycle integration

`nexus_personal.egress_policy.render_policy()` generates an atomic nftables
batch for the dedicated `inet nexus_agent_egress` table. Its input contains
`schema_version=1`, all managed bridge `interfaces`, exact `tcp_endpoints`
(`address`, `port`) and explicit `dns_servers`. It does not resolve domains,
accept arbitrary nft syntax, execute commands or set any readiness flag.
Addresses must be resolved and approved by the operator; DNS/IP changes require
an explicit policy update. No wildcard Internet egress is granted.

The reference denies other destinations/ports, host services and cross-Agent
forwarding; approved DNS only allows TCP/UDP port53. Updating the policy also
re-evaluates existing outgoing connections. Only the named table is replaced;
an invalid nftables transaction must leave its previous policy installed.
See the [nftables reference](https://netfilter.org/projects/nftables/manpage.html)
for transaction and hook semantics.

Real IPv4/IPv6 regression lives in `tests/test_egress_policy.py`, with an opt-in
anonymous Linux namespace fixture. It first proves every target reachable,
then checks filtering, DNS, peer isolation, established-flow revocation,
failed updates and recovery; it verifies original namespace rules after cleanup.
Ordinary tests do not acquire network administration privileges.

An explicitly configured `controllers.agent.network_policy` enables the Personal
Linux worker integration. This optional object has exactly these fields:

| Field | Meaning |
| --- | --- |
| `nft_executable` | Absolute, operator-protected nftables executable outside workload storage |
| `tcp_endpoints` | Approved literal IP/port pairs, in the renderer format above |
| `dns_servers` | Approved literal resolver addresses; TCP/UDP port 53 only |

The worker installs its own per-instance table before creating a Docker bridge.
A reserved, deterministic `nx<worker-hash><network-hash>` interface name lets the
rule cover future generations before their first process starts. Peer traffic
to other reserved worker bridges is denied even when that IP is an approved
endpoint. The integration checks bridge ownership and exact container network
membership on startup, adoption and health checks; incompatible old containers
require explicit redeployment, not silent network conversion.

Managed containers use Docker restart policy `no`: the Controller restores the
rules before restarting a stopped generation. Host-initiated MCP replies remain
allowed; Agent-initiated host connections still require an approved endpoint.
Policy installation failures prevent startup. Emergency stop does not depend on
the firewall installer. The integration never invokes sudo, changes privileges,
switches namespaces or alters host sysctls. Container-proxy Controllers are not
supported by this optional backend. Without it, the existing externally managed
policy contract and Enterprise behavior remain unchanged.

The namespace fixture also exercises real backend installation before interface
creation and restoration after deletion of its own table. This is not yet real
Docker/Cloud callback acceptance. Do not set `egress_policy_ready=true` solely
because renderer, sequence or namespace tests pass. A dedicated worker must
still pass the real daemon, DNS and callback release gates. The installer does
not automatically install a firewall or declare production readiness.

Start each configured Controller as its own process:

```text
python -m nexus_personal.processes agent-controller
python -m nexus_personal.processes provider-controller
```

The launchers assign their dedicated process roles and reuse the original
management commands, real Docker dependency checks and authenticated protocol.
No fake runner or successful health stub substitutes for Docker. Controller
commands reject worker `--concurrency` and `--once` options. Use the same protected
configuration for Web, workers and Controllers; token changes require coordinated
restart of those processes. Automated provisioning, rotation and supervision are
still pending, not silently performed by configuration loading.

## Administrative entrypoint

From the Server source root with the explicit protected configuration selected:

```text
python -m nexus_personal.manage check
python -m nexus_personal.manage migrate --noinput
python -m nexus_personal.manage setup_personal --email <owner-email>
```

The final command prompts securely for the password. Unattended setup may use
`--password-stdin` with a protected pipe, never a password command argument.
Existing identities are not adopted or reset. Setup creates one owner/context,
not IAM roles, wallets or plans. Do not run these against an Enterprise database.

## HTTP/ASGI boundary

Personal's tenancy compatibility service exports only the existing fixed-owner
request-context resolver. It does not expose Organization/Project/Group creation,
membership, offboarding or management roles. Enterprise selects its unchanged
administrative service, serializer, permission and URL implementations explicitly.
Context models/migrations are unchanged by this source relocation.

Likewise, Personal uses shared Provider connection/import modules and its
owner-only historical recovery routes, not the mixed legacy Provider HTTP host.
That host is configured only by Enterprise and contains publication, commercial
Pool catalog/moderation and usage endpoints. A missing host setting does not
activate these APIs or relax a permission check.

The legacy Provider serializer module is likewise host-selected and not enabled
in Personal. Shared input validation and Personal connection/Offer presentation
remain separate. Upstream text/image prices are operational cost facts and remain
available; Marketplace settlement amounts are not part of Personal presentation.

Observability remains a shared frontend route with Personal operational system,
capabilities, alerts, monitoring Jobs and Audit endpoints now composed. Their
database and import-isolation tests do not establish a live deployment.
Rendered/deployed end-to-end acceptance is still outstanding; a working shell
or build does not prove that the complete page and worker fleet are functional.

Personal composes dedicated shared Agent URL modules directly. The historical
`apps.agents.views/urls` and `runtime_views/runtime_urls` compatibility modules
require explicit process-level HTTP host settings; Personal intentionally does
not configure those mixed compositions. Importing them fails closed instead of
loading Enterprise pricing, publication or billing callbacks. Core shared view
classes remain independently importable. Enterprise owns its complete legacy
compositions and retains their original URL include names and callback identity.

`nexus_personal.asgi:application` initializes the actual Django HTTP and Computer
Runtime/Terminal WebSocket handlers. Cookies require HTTPS; CSRF middleware and
owner/session authentication remain active. No public signup is exposed.

The listener must remain on loopback behind a trusted HTTPS reverse proxy. The
proxy must replace, not append untrusted, `X-Forwarded-Proto` headers and support
WebSocket Upgrade. Configure the ASGI server to trust only that proxy and disable
access logs containing URL query strings. This host's forwarded-header setting
must not be used with a directly Internet-exposed HTTP listener.

Do not use an Enterprise `config.celery` process. The personal Celery application
has a distinct Redis key namespace and does not inherit Cloud schedules or
autodiscover the mixed repository. Its explicit operational inventory currently
includes Provider import cleanup, catalog refresh and health reconciliation,
Source health, Dataset dispatch/cleanup, Agent file transfers and image expiry.
Provider maintenance retains the actual refresh/recovery logic; commercial
settlement statistics are supplied only by the Enterprise adapter.
The Inbox repair job is also registered: it restores missed Run/question/build/
import notifications and operational issues, rechecks active sources, and prunes
terminal items after retention. It uses the original bounded cursor protocol and
PostgreSQL advisory lock. Personal records target the installation owner; no
role audience or commercial workflow source is included.
The twelve scheduled jobs also include the real Docker desired-state reconciler,
Edge presence expiry, and Personal invocation lease expiry. The latter releases
local Run capacity, never funds; durable tasks remain owned by the existing
Agent worker. Legacy simulated deployment and commercial billing cleanup tasks
are not registered in the Personal application. Presence heartbeat state belongs
under this installation's private `run/presence-sweeper.json`.

When integrating the host, the Celery worker must consume **both**
`personal-default` and `dataset-imports` from this installation's broker namespace:

```text
python -m nexus_personal.processes worker
python -m nexus_personal.processes beat
python -m nexus_personal.processes agent-worker
```

These are process entrypoints, **not** an installation or service supervisor.
These launchers set the correct process role before initializing production
settings, including when an inherited role is `web`. They reject an Enterprise
settings selection or already-initialized Django. Windows uses the Celery solo
pool with concurrency one; other platforms default to four. Agent invocation
worker concurrency defaults to four. Use `--concurrency` for explicit supported
limits; `agent-worker --once` runs one existing worker cycle for diagnostics.
ASGI continues to use the `web` role. Do not invoke raw Celery commands without
the correct role: production heartbeat validation rejects mismatched processes.
Run only one Beat scheduler per installation. Its persistent schedule belongs
under the private state directory's `run/personal-beat`; provision that parent
directory with private permissions first. Singleton supervision, live Redis
delivery/reconnect and deployment acceptance remain outstanding. Agent invocation
execution uses the existing durable PostgreSQL worker (`run_agent_tasks`), not
Celery or a simulation task. Web Push delivery (including protected VAPID
configuration) and remaining lifecycle maintenance still need explicit
integration before this is a complete worker host. In-app Inbox repair is not a
claim that browser push delivery is configured or verified. Stale Provider
start/stop recovery retains its existing database-wide selection; do not treat
the per-owner catalog filter as a general multi-owner worker isolation boundary.

An HTTP cold boot or direct task test does not prove background work is served.
Unconfigured Controller sockets resolve under the private state directory;
configured Controllers use their explicit loopback ports. Neither falls back to
fake runners or Cloud controllers. Controller and isolated Python-builder
service provisioning remain operational prerequisites; the protected
`python_builder` configuration above now supplies the explicit enablement path.
Docker deployment preparation now uses the common host-selected admission
interface. Personal defaults to a finite per-container ceiling of 2 vCPU and
2048 MiB in `NEXUS_PERSONAL_AGENT_LIMITS`; these are local safety limits, not
commercial plans or total-host resource reservations. The actual container
CPU/memory arguments retain the existing normalization and enforcement. Missing,
invalid or exceeded local limits fail before deployment state is created.
Configurable operator capacity and Controller installation still need end-to-end
integration; passing admission tests does not prove container launch or recovery.

The frontend path points into this installation's state directory, never the
Enterprise build in `nexus_server/static/web`.

### Community browser build

From `nexus_web`, run `npm run build:community` after installing the locked Web
dependencies. This type-checks the Personal entry and produces `dist/community`
with `index.html`, hashed assets, brand files and same-origin PDF fonts/CMaps.
The build rejects private module dependencies and enumerated commercial API
references, including lazy chunks. It cannot be used as a development proxy to
the formal Cloud API. Ordinary `npm run build` still selects Enterprise.

The installer must place the complete bundle, including `community-assets.json`,
in the configured Personal frontend directory (`<state_dir>/web`). The Personal
production middleware now serves its known application routes, `/static/web/`,
brand files, examples and the root-scoped Inbox service worker. API/WSS paths
are never replaced with HTML; missing assets return 404. Do not copy this bundle
over formal Cloud assets or serve it against Enterprise APIs. The
Personal entry uses the existing owner session plus `/api/v1/personal/context/`;
there is no Organization directory/selector or browser-side edition switch.

The build writes an edition-tagged size/SHA-256 inventory after successful
boundary validation. The host reads only inventoried regular files, rejects
links/junctions, traversal, source maps and corrupt/missing assets, and retains
one bounded immutable snapshot per process (4,096 files, 32 MiB per file, 64 MiB
total). This inventory detects corruption and accidental edition mix-ups; it is
**not** a publisher signature, secret scan or source-release approval. Keep the
web directory operator-controlled and separate from Computer/Agent storage.
Deploy the entire release then restart web workers; changing files underneath a
running worker does not change its snapshot. HTML and service workers use
no-store; other assets use ETag revalidation. PDF fonts remain same-origin.

After installing the protected host config and frontend, the explicit web entry
is `python -m nexus_personal.processes web --port <dedicated-port>` (optional
`--web-workers 1..16`). It validates the frontend before listening and fixes the
ASGI target and role, loopback address, no reload and disabled access logs. Put
the existing HTTPS proxy in front, replacing forwarded headers and supporting
WSS upgrades. It does not provision that proxy, reserve ports, supervise other
services or install PostgreSQL/Redis. Never expose this HTTP port directly.

The production bundle and compiled-shell browser checks now pass, but the
browser checks use mocked HTTP responses. Automated installation, complete
shared-page/Personal API compatibility and production HTTP/WSS/worker/Controller
acceptance remain unfinished. A successful build is not an installable release.

## Hosted Agent management composition

Personal composes caller-owned Computer/Mobile bindings and explicit scope
grants, plus Run Computer browsing/switching and Terminal tickets. Computer
pairing, capability/presence admission, caller isolation and Display Token
validation use the existing shared implementation. Attach does not implicitly
grant scopes. Organization Project-context grants remain uncomposed.

The existing in-process ASGI/real SDK Runtime integration now obtains scope
grants and bindings through HTTP, browses folders and PATCHes the active Run's
folder instead of directly changing its database row. The Runtime actually
validates the temporary directory, reads files relative to the new cwd and
executes the existing real command. This covers the commit/wakeup boundary,
not deployed network WSS/TLS, a physical phone or a hosted Docker Agent.
Remote folder validation runs outside the final short Run transaction so the
queued command can commit; before saving cwd the service rechecks caller
authority and the original Computer revision/binding/root. Device replacement
does not migrate files. Run context extensions own any extra credential clearing;
Personal never manufactures commercial token fields.

Personal also composes the existing owned repository-metadata initialization/
push, version list/publish/rollback and resource-configuration routes. Version
publication here means an Agent revision, not Marketplace publication. Resource
updates use the shared Docker unit parser and Personal CPU/memory admission;
invalid or excessive settings leave the previous configuration intact.
Repository push currently records existing metadata behavior; it does not
store/build an executable artifact or prove repository-backed deployment.
These HTTP paths retain real owner/fixed-Project and CSRF enforcement. Commercial
pricing, visibility, publication and dedicated commercial keys are not composed.

Personal also composes shared Agent logs, status, bounded Run history/events,
redaction, output listing/scanning and generic MCP configuration export.
Operational event history reads the saved redaction and applies current secret
rules without mutating original evidence or the caller's Private Display.
MCP export is a configuration template; it does not create a commercial API key.
Output HTTP tests use the existing temporary upload and scan path, not a fake
scanner. These tests do not prove image decoding or live container execution.

Personal exposes the shared image inventory/selection/removal, deployment,
stop, health-check, MCP export and Python-build management HTTP routes. They
use the existing Controller and durable lifecycle Jobs. Job list/detail/events
are owner- and fixed-Project-scoped, without an IAM administrative role or
commercial service dependency. A queued response records desired work, not
proof that Docker has started or stopped a container; queue-dispatch failure
retains the existing reconciliation path and visible deferred event.

Python building remains disabled in the host defaults. Deploying a real image
still requires the protected Controller configuration, admission verifier,
verified egress policy, working Docker Engine and workers described above.
HTTP composition does not provision these prerequisites. Tests seed admitted
image records and check real database state/HTTP authorization while replacing
only queue dispatch; they are not image-scan or container-execution evidence.

## OpenWrt Edge composition

Personal composes the existing pairing, device enrollment, presence and Agent
registration APIs. They bind to the durable installation owner and fixed
Project, not IAM roles or a commercial subscription. Re-enrollment rotates the
device token and generation while retaining its Node and managed Agent identity.
Device enrollment responses do not grant browser-console permissions.

Production Edge settings explicitly require verified mTLS ingress headers and
installation-local material under `state_dir/keys`: `edge-signing.pem`,
`edge-device-ca.pem`, `edge-device-ca-key.pem`, `edge-ingress-ca.pem` and
`edge-ingress-ca-key.pem`. The JWT issuer is the Personal public origin plus
`/edge`. The Relay bootstrap provisions the device CA and Edge signing key. Operators must still provision the ingress CA and a proxy that
validates device certificates and strips/replaces client-supplied internal
headers. Relay bootstrap is not a completed HTTPS/mTLS ingress installer. Never copy the formal Cloud's CA/device/signing keys
or turn off verification to make enrollment pass.

Community launchers now provision and start the bundled Relay, with installation-owned
device CA, Edge signing key and Relay credentials. Relay settings are loaded from the
protected installation relay/settings.json; inherited Enterprise variables are not used.
Native launchers require Node.js on PATH. The Docker image includes the runtime.
The tunnel defaults to local-only port 27444; Cloud invoke stays internal on 27445.
See the source README for --relay-address / -RelayAddress and Docker address options.
Existing installations without Relay settings still report unconfigured. Actual Direct IPv6 and Relay invoke,
certificate provisioning/rotation, and independent deployment remain release
gates. Backend tests with verified proxy headers and compiled-page tests with
mocked HTTP are not evidence of real network TLS or router readiness.

## Release readiness

This guide describes integration entrypoints, not a completed release installer.
Before production use, independently verify the prepared configuration, HTTPS/WSS
proxy, PostgreSQL/Redis isolation, process supervision, controller lifecycle,
image admission, IPv4/IPv6 egress, and Agent/Computer workloads. Edge operation
additionally requires this installation's own enrollment and TLS material.

Unit tests, local artifact acceptance and mocked browser responses are not
interchangeable with a deployed end-to-end result. Historical internal test
checkpoints are not shipped as operator instructions. Publish only reviewed
artifacts with current dependency, license, secret and source-boundary approval;
never publish the mixed development repository or its history.
