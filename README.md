# Nexus Cloud Community

A single-owner workspace for running Agents, routing model requests, managing
Data Assets, and connecting your own Computer and Mobile devices. This release
contains the Cloud Server and Web Console only.

Nexus-authored source uses [Apache-2.0](LICENSE).
Separately licensed upstream components retain their notices and licenses.

## Source layout

- `nexus_server/`: Community host and shared execution services.
- `nexus_web/`: Community Console.

OpenWrt (including edge routing, Relay and LuCI), Mobile, and the Python Agent
SDK / Computer Runtime are independently released projects. Their source,
installers and firmware are not bundled here. Cloud-side integration endpoints
remain available; install compatible client/device releases separately when
using those integrations. The Server and Web source builds do not require
checking out those projects.

Commercial billing, TokenBank, Access administration and the three commercial
Marketplaces are not part of this source distribution. Community remains
authenticated: one owner does not mean anonymous access to devices or files.

## Build from this source export

### One-command Docker deployment

With Docker Engine / Docker Desktop (Linux containers) and Compose v2, run from
this source root:

```sh
docker compose -f deploy/community/compose.yaml up -d --build --wait
```

Open `http://127.0.0.1:18090`. See the
[Docker deployment guide](deploy/community/README.md) for the generated owner
password, named-volume persistence, HTTPS/WSS deployment and execution-worker
requirements. This builds Community Server and Console only; no enterprise
services or separately released device/SDK sources are included.

### Native development and installation

For Linux, follow the complete [native development guide](nexus_server/nexus_personal/LINUX.md)
for venv setup, Server/Web builds, PostgreSQL/Redis, owner initialization and
`bash ./start-nexus-community.sh start --installation <absolute-directory>`.
The Linux launcher also supports `stop`, `restart`, `status` and `check`.

Use a new Python 3.14 environment and Node.js 24. Do not reuse an existing
commercial installation, database, credentials or dependency environment.

From `nexus_server/`, install the build and runtime dependency locks described
in the [operator guide](nexus_server/nexus_personal/HOST.md), using the matching
`win-py314` or `linux-py314` files. Then build the Server without resolving
additional dependencies:

```text
python -m pip wheel --no-deps --no-build-isolation . --wheel-dir dist
```

On Windows, after preparing and initializing the dedicated installation described below,
start all configured Community processes from the source root:

```powershell
.\start-nexus-community.ps1 -InstallationDirectory C:\Nexus\Community
```

Use `-Restart`, `-StopOnly` and `-CheckOnly` for lifecycle and diagnostics. The
launcher serves the verified Web bundle through the Community backend, so it
does not start Vite. Its HTTP listener is loopback-only and must remain behind
the installation's HTTPS/WSS reverse proxy. It does not use Enterprise Cloud,
OpenWrt/Relay, Docker provisioning or a shared database.

For a disposable test installation whose configured origin is the same
`https://127.0.0.1:<port>`, add `-LocalHttp`. It exposes only
`http://127.0.0.1:<port>` and never listens on another interface. This avoids a
local certificate prompt, but is not a production deployment mode.

From `nexus_web/`:

```text
npm ci --ignore-scripts
npm run build:community
```

The Console output is `nexus_web/dist/community/`. Keep its
`community-assets.json`, PDF assets and `THIRD_PARTY_NOTICES.txt` together.
Do not use the mixed-development Console entrypoint.

## Run your own installation

Follow the [operator guide](nexus_server/nexus_personal/HOST.md) for dedicated
PostgreSQL/Redis, HTTPS/WSS, private host configuration, owner initialization,
workers and controllers. Preparing configuration is not proof that these
services are running. Device pairing does not disable scope or caller checks.

Start with [Community workflows](nexus_server/nexus_personal/WORKFLOWS.md).
For client installation, use the guide provided with the separate SDK release.
See [release notes](RELEASE_NOTES.md) before enabling container or Edge workloads.

## Contributing

Changes to shared code should preserve the public SDK interfaces, independent
Community composition, and host-selected extension contracts. Never introduce
a dependency on an unavailable private implementation. Use synthetic test data;
do not include tokens, device keys, personal files or deployment configuration.

Submit contributions under Apache-2.0 unless explicitly agreed otherwise.
Report suspected security issues privately to the repository maintainers before
posting exploit details or credentials in a public issue.

## Source integrity

`community-source-manifest.json` records each exported file and its SHA-256.
It detects changed bytes; it is not a publisher signature. Obtain the archive
and its checksum from a trusted release channel. This export contains no Git
history, local deployment state, environment files or private commercial tree.
