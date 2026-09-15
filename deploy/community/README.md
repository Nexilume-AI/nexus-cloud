# Nexus Community with Docker Compose

Run from the root of the clean Community source release. Docker Desktop with
Linux containers or Docker Engine with Compose v2 is required (x86_64, at least
4 GiB available memory recommended). The mixed development repository is not a
build context: the image rejects private host/application directories.

```sh
docker compose -f deploy/community/compose.yaml up -d --build --wait
```

Open **http://127.0.0.1:18090**. The initial account is `owner@example.local`.
Retrieve its generated password explicitly (it is never emitted in startup logs):

```sh
docker compose -f deploy/community/compose.yaml run --rm --no-deps --entrypoint cat initialize /var/lib/nexus-bootstrap/owner-password
```

Change the account password in Settings after signing in. The saved bootstrap
password is only the initial credential; restarting never resets your password.
Set `NEXUS_OWNER_EMAIL` before the first start to choose a different owner email.
To change the local port, set **both** `NEXUS_PORT=19090` and
`NEXUS_ORIGIN=http://127.0.0.1:19090` before first initialization.

The single image includes the compiled Console and installed Community Server.
Compose runs Web, background workers, durable Agent worker and Beat separately,
with PostgreSQL, Redis and a WebSocket-capable gateway. Database/configuration,
encryption keys, files and Redis persistence have separate named volumes.
Application processes run as UID 10001 with a read-only image. The one-time
preparation process drops privileges before generating installation secrets.

```sh
# Inspect or follow service logs
docker compose -f deploy/community/compose.yaml ps
docker compose -f deploy/community/compose.yaml logs --tail 100 web worker
# Stop and start without deleting data
docker compose -f deploy/community/compose.yaml down
docker compose -f deploy/community/compose.yaml up -d --wait
```

Do not use `down -v` unless you intend to erase this installation. Back up
PostgreSQL together with `community-state` and `bootstrap`: encrypted content
cannot be recovered from the database alone. An interrupted initial migration
fails explicitly for inspection; startup does not erase or adopt another database.

The default HTTP port is published only on host loopback. Database and Redis
listen only inside this stack's shared network namespace. For remote Computers
and other machines, set `NEXUS_ORIGIN=https://your-domain.example` before the first
start, point DNS to this host, allow ports 80/443, and run (Compose >= 2.24.4):

```sh
docker compose -f deploy/community/compose.yaml -f deploy/community/compose.https.yaml up -d --build --wait
```

Caddy obtains and renews the public certificate and supports WSS. This is a
separate installation choice, not an in-place origin change for a paired device.
Include `caddy-data` in backups. Public DNS/certificate issuance requires an
operator-owned reachable domain; the local HTTP test does not verify it.

This deploys Cloud itself. OpenWrt, Mobile and the Python SDK are separate
distributions. Docker-hosted Agent/Provider controllers are separate execution
services and are not implicitly granted the host Docker socket by this stack.
Python upload requires the existing operator-configured builder/controller profile;
the Cloud service image is not an Agent runtime image. See the Community host guide
for that profile. Pairing Computer Runtime uses the deployed Cloud API and WSS.

For code upgrades, back up first, rebuild the image, run the installation's
reviewed migration procedure, and recreate all namespace-sharing services together.
Do not independently recreate only the PostgreSQL namespace container. Existing
installations deliberately refuse changed owner/origin or unreviewed schema upgrades.

## Relay startup

The normal startup now includes the bundled Relay. Credentials are generated per
installation and reused on restart. Native launchers require Node.js on PATH.
For router access, select a reachable IP on first start with `--relay-address`
(Linux), `-RelayAddress` (Windows), or `NEXUS_RELAY_ADDRESS` plus
`NEXUS_RELAY_BIND` (Compose). Without these options access is local-only.
Tunnel port: 27444; mTLS/JWT Cloud ingress: 27445, internal only. No firewall,
public IPv6, or verified-device-mTLS Cloud proxy is automatically configured.
See the source README and HOST.md for onboarding prerequisites.
