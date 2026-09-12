# Linux native development

This workflow runs Community Server and Web directly on Linux, using a Python
virtual environment. Docker is not required for Cloud itself. Container-based
Agent/Provider workloads still require separately configured execution services.
Use the clean Cloud Community source release, CPython 3.14 and Node.js 24.
Run application commands as your normal user, not root. Ubuntu 24.04 is the
acceptance target; other distributions may use different package/service names.

## 1. Build Server and Console

Install Python 3.14 with venv support and Node.js 24 using your normal toolchain
manager. Ubuntu 24.04's default Python 3.12 is too old. Confirm `python3.14
--version` and `node --version` before continuing. In WSL, use Linux executables,
not Windows Python/Node on `/mnt/c` or `/mnt/d`, and keep the checkout and venv
in the Linux filesystem.

From the extracted source root:

```sh
export NEXUS_SOURCE="$PWD"
python3.14 -m venv .venv
. .venv/bin/activate
cd nexus_server
python -I -m pip --isolated install --index-url https://pypi.org/simple --require-hashes --only-binary :all: --no-cache-dir --disable-pip-version-check -r nexus_personal/build-requirements-linux-py314.txt
python -I -m pip --isolated install --index-url https://pypi.org/simple --require-hashes --only-binary :all: --no-binary http-ece --no-build-isolation --use-pep517 --no-cache-dir --disable-pip-version-check -r nexus_personal/requirements-linux-py314.txt
python -I -m pip check
python -m pip wheel --no-deps --no-build-isolation . --wheel-dir dist
python -m pip install --no-deps dist/nexus_community_server-*.whl
cd ../nexus_web
npm ci --ignore-scripts
npm run build:community
cd "$NEXUS_SOURCE"
```

`http-ece` is the explicit source-build exception in the locked dependencies.
Keep the complete `nexus_web/dist/community` directory, including its manifest
and third-party notices. The launcher runs the source Server and the verified
compiled Console; it does not start Vite or automatically rebuild changed files.

## 2. Provision a dedicated database and Redis

On a new Ubuntu development machine, an administrator can install the services:

```sh
sudo apt-get update
sudo apt-get install postgresql redis-server
sudo systemctl start postgresql redis-server
sudo -u postgres createuser --pwprompt nexus_community
sudo -u postgres createdb --owner=nexus_community nexus_personal_dev
```

Use a new PostgreSQL role/database and reserve an unused Redis database number
from 1–15. The example below uses Redis database 1; change it if already used.
Do not reuse a commercial Cloud database, role or Redis database. Redis must be
reachable only from trusted local processes; remote database/Redis connections
require the TLS configuration described in [HOST.md](HOST.md). A WSL installation
needs its database services running as well; the Cloud launcher does not manage
system services.

## 3. Prepare and initialize

The example uses loopback HTTP for local development. Choose the port and origin
before initialization; the configured HTTPS origin and `--local-http` port must
match. For access from other devices, use your real HTTPS origin and reverse
proxy from the beginning, and omit `--local-http` when launching.

```sh
umask 077
mkdir -p "$HOME/.local/share/nexus"
export NEXUS_INSTALLATION="$HOME/.local/share/nexus/community-dev"
export NEXUS_INFRASTRUCTURE="$HOME/.local/share/nexus/infrastructure-dev.json"
python - <<'PY'
import getpass, json, os
config = {
    "schema_version": 1,
    "database": {
        "name": "nexus_personal_dev", "host": "127.0.0.1", "port": 5432,
        "user": "nexus_community", "password": getpass.getpass("PostgreSQL role password: "),
        "sslmode": "disable"
    },
    "redis_url": "redis://127.0.0.1:6379/1"
}
fd = os.open(os.environ["NEXUS_INFRASTRUCTURE"], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as output:
    json.dump(config, output)
PY
python -m nexus_personal.install prepare \
  --directory "$NEXUS_INSTALLATION" \
  --origin https://127.0.0.1:18080 \
  --infrastructure-file "$NEXUS_INFRASTRUCTURE" \
  --web-bundle "$NEXUS_SOURCE/nexus_web/dist/community"
python -m nexus_personal.install initialize \
  --directory "$NEXUS_INSTALLATION" --email owner@example.local
```

Initialization prompts for the new owner's password. Save it securely; there is
no default password. Preparation refuses an existing installation destination,
links and permissive file permissions. Initialization refuses an unrelated or
partially migrated database. Follow [HOST.md](HOST.md) for recovery instead of
deleting migration state or switching an existing installation's settings.

## 4. Start, inspect and stop

From the source root, with the venv activated:

```sh
bash ./start-nexus-community.sh start --installation "$NEXUS_INSTALLATION" --local-http
bash ./start-nexus-community.sh status --installation "$NEXUS_INSTALLATION"
bash ./start-nexus-community.sh check --installation "$NEXUS_INSTALLATION" --local-http
bash ./start-nexus-community.sh restart --installation "$NEXUS_INSTALLATION" --local-http
bash ./start-nexus-community.sh stop --installation "$NEXUS_INSTALLATION"
```

Open **http://127.0.0.1:18080** and sign in with the owner credentials. To use a
different interpreter, set `NEXUS_COMMUNITY_PYTHON` to the venv's absolute Python
path. `--port` selects a loopback backend port; `--web-workers` defaults to 1.
Pass the same port and HTTP choice on every start/restart/check command.

The launcher starts Web, Celery worker, durable Agent worker and Beat, plus only
the controllers/Python builder explicitly configured in this installation.
Processes survive closing the invoking terminal. It records process identity in
`run/community-processes.json`; a reused PID is never intentionally stopped.
`status` reports tracked process liveness, not external workload readiness.
`check` validates installation files and pending migrations, not complete service
health. Startup waits for the bootstrap HTTP endpoint; it does not certify a
connected Computer, execution controller or external provider.

Logs are private files in `$NEXUS_INSTALLATION/run/logs/`:

```sh
tail -n 80 "$NEXUS_INSTALLATION/run/logs/web.log"
tail -n 80 "$NEXUS_INSTALLATION/run/logs/worker.log"
```

Stop uses graceful process-group shutdown and retains data. If shutdown times
out, state is retained for inspection; do not remove it while processes remain.
An occupied port or pending migration makes startup fail explicitly. After
reviewing an upgrade and backing up the database and installation together,
stop the application and apply migrations explicitly:

```sh
export NEXUS_PERSONAL_CONFIG="$NEXUS_INSTALLATION/host.json"
python -m nexus_personal.manage migrate --noinput
```

This launcher is for development and manually managed installations. It is not
a system service supervisor: it does not restart crashed processes, rotate logs
or configure boot startup. For production, supervise the documented
`nexus-personal-process` roles with your service manager, retain the installation
owner and environment, and provide HTTPS/WSS as described in [HOST.md](HOST.md).
