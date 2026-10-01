# Optional Provider execution environment

The Providers page calls the engines **Direct API**, **Codex Proxy** and
**CLIProxyAPI**. A connection's name is your own label, not its engine name.
Codex Proxy is the Nexus-managed proxy engine; it is not the Codex desktop app.
CLIProxyAPI supports browser sign-in to OpenAI or Claude and exposes an
OpenAI-compatible endpoint to Nexus.

| Installation | What you need |
| --- | --- |
| Direct API connection | An existing compatible HTTPS API endpoint and its credential. No Provider Controller or Docker is needed. |
| Native Cloud + Codex Proxy / CLIProxyAPI | Docker CLI and a running Linux Docker Engine, verified Provider images and receipts, and the opt-in Provider Controller below. |
| Compose Cloud + Codex Proxy / CLIProxyAPI | The same verified images and receipts, plus the explicit Compose overlay below. |

**Docker is a host prerequisite, not a Python requirement.** Installing the
Server wheel or `nexilume` SDK does not install Docker Engine. Install Docker
Desktop (Linux containers) or Docker Engine + Compose v2 yourself. The default
Cloud Compose file never mounts the Docker socket. Check the engine with
`docker version --format '{{.Server.Os}}'`; it must return `linux`.

## Verified Provider releases

Before enabling an engine, obtain its reviewed Nexus image and matching release
receipt from your operator's Provider release pipeline. Load an image archive
using `docker load --input /absolute/path/to/reviewed-provider-image.tar` on the
execution host. Keep receipts in a dedicated operator-controlled directory,
separate from workload data, named `codex_proxy.json` and/or `cliproxyapi.json`.

Receipts must use `nexus-provider-release-v1` and record the verified gates,
immutable local image SHA-256 ID, revision and matching image labels. They are
operator approvals, **not cryptographic signatures**. Never accept receipts
uploaded by a Provider user, fabricate a receipt, or substitute an upstream
`latest` image. Setup checks the actual local image ID and labels; it never
pulls/builds a Provider image or automatically upgrades an existing runtime.

This repository does not currently publish a ready-to-pull verified Provider
image release. If you do not have an approved image and receipt, use Direct API
or ask the operator for the release artifacts; the optional setup deliberately
stops rather than trusting an arbitrary image. The Cloud image is not a Provider
image, and Docker's CLI image below is only a tool dependency.

## A. Native installation

Use the Python environment and installation directory from your existing
Community host setup. First run a read-only check (choose one or both engines
with repeated `--engine` flags):

```sh
python -m nexus_personal.install enable-provider-runtime --directory /absolute/path/to/community --release-dir /absolute/path/to/provider-releases --engine codex_proxy --check
```

If the check succeeds, stop Community using its normal supervisor or
`start-nexus-community.ps1 -InstallationDirectory <directory> -StopOnly`,
then run the same command without `--check`:

```sh
python -m nexus_personal.install enable-provider-runtime --directory /absolute/path/to/community --release-dir /absolute/path/to/provider-releases --engine codex_proxy
```

PowerShell accepts the same flags; use quoted absolute Windows paths, for
example `--directory 'D:\Nexus\community'`. No Docker install, service restart
or image download is performed by this command. It selects a free loopback
Controller port and generates a private authentication token. Repeating it
preserves the existing port/token. Other controllers, keys, databases and
Python upload configuration are retained. Configuration, installation receipt
and database identity are updated together; failure leaves the old setup intact.

Restart with `start-nexus-community.ps1 -InstallationDirectory <directory>` or
the existing Linux launcher/supervisor. The official launcher starts a configured
Provider Controller automatically. Reopen Providers and select **Check again**.
Only verified engines become available; a missing CLIProxyAPI release does not
prevent using an installed Codex Proxy release.

## B. Explicit Compose option

Run from a clean Community source checkout, not the mixed Enterprise development
repository. Stop an existing stack using its original Compose command, without
`-v`. This briefly stops Cloud; never change namespace-sharing services one at a
time. Retain the same Compose project name, origin, owner and volumes.

Create two dedicated directories: an initially empty Provider data directory
and an operator-controlled release receipt directory. Set these non-secret
variables before running Compose (PowerShell uses `$env:NAME = 'value'`):

The receipt directory/files must be readable by container UID 10001 (for
example, directory mode 0755 and receipt mode 0644 on Linux), but only the
operator may modify them. Receipts contain image metadata, not account secrets;
never place credentials in this directory. Setup does not loosen their ACLs.

```sh
export NEXUS_PROVIDER_DATA=/absolute/path/to/provider-data
export NEXUS_PROVIDER_RELEASES=/absolute/path/to/provider-releases
export NEXUS_PROVIDER_ENGINES=codex_proxy
# Use codex_proxy,cliproxyapi only when BOTH verified images are installed.
docker compose -f deploy/community/compose.yaml build prepare
docker compose -f deploy/community/compose.yaml -f deploy/community/compose.providers.yaml build provider-controller
docker compose -f deploy/community/compose.yaml -f deploy/community/compose.providers.yaml up -d --wait
```

On Docker Desktop, use absolute Windows host paths in the environment variables;
the Controller resolves Docker's translated mount source automatically. A remote
Docker daemon must have these directories on its own host and is not installed
or configured by this recipe. The official overlay expects the local Linux
Docker socket at `/var/run/docker.sock`.

For an existing HTTPS installation, retain `compose.https.yaml` in every
command alongside `compose.providers.yaml`. If you changed
`NEXUS_COMMUNITY_IMAGE`, keep that value during both builds. Pin
`NEXUS_DOCKER_CLI_IMAGE` to your reviewed Docker CLI image digest in production.

The overlay adds a one-shot `provider-setup` and a supervised
`provider-controller`. Only these trusted services mount the Docker socket;
Web, workers and Relay do not. **Docker socket access is effectively host-root
authority**, even though the Controller drops to UID 10001. Opt in only on a
trusted execution host. The bootstrap only adopts an empty dedicated data
directory; it does not recursively change permissions on existing host files.

Provider containers use a dedicated Docker network and are addressed by container
DNS internally, not published Provider API ports. Keep
`NEXUS_PROVIDER_NETWORK` unique if you run several stacks on one Docker Engine.
The shared Cloud database and Redis remain bound to loopback.

Use the overlay for subsequent starts/stops and backups. Back up the Provider
data directory and verified receipts as well as the normal Cloud volumes.
Once enabled, do not omit the overlay: the installation references its protected
receipt mount. Disabling individual Providers remains available in the UI.

## Troubleshooting

| Setup status | Recovery |
| --- | --- |
| Controller unconfigured | Run the native opt-in command or use the Compose overlay. |
| Controller unavailable | Start the configured Controller with the normal launcher; check its service status. |
| Docker CLI missing / Engine unavailable | Install the CLI or start Linux Docker Engine; verify the Controller user's Docker access. |
| Release required | Install the matching operator-approved receipt, not arbitrary upstream metadata. |
| Image unavailable | Load the exact approved image and verify its labels/ID; mutable tags are insufficient. |

Click **Check again** after fixing the host. Failed checks do not start a
container, change Provider state, reveal Docker diagnostics or rotate credentials.
Browser sign-in still requires your upstream account after installation succeeds.
