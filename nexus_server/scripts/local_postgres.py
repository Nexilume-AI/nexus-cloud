"""Provision/start the dedicated Docker database, never the disposable QA database.

Runtime credentials stay in ignored, owner-restricted local state. No passwords,
connection strings, Docker stderr or database exception details are printed.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time

import psycopg
from psycopg import sql

SERVER = Path(__file__).resolve().parents[1]
CONFIG = SERVER.parent / ".local/nexus-cloud/postgres.json"
CONTAINER = "nexus-cloud-postgres"
VOLUME = "nexus-cloud-postgres-data"
LABEL = "com.nexilume.managed"
# Lock diagnostics without logging SQL or bind values. These settings require
# administration privileges, never grant them to the application role.
LOCK_DIAGNOSTICS = {
    "log_lock_waits": "on",
    "log_min_error_statement": "panic",
    "log_error_verbosity": "terse",
    "log_parameter_max_length": "0",
    "log_parameter_max_length_on_error": "0",
}


def docker(*args, env=None):
    executable = shutil.which("docker") or r"D:\Docker\Desktop\resources\bin\docker.exe"
    result = subprocess.run([executable, *args], env=env, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError("Docker operation failed (details suppressed to protect credentials)")
    return result.stdout.strip()


def private_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2)
    if os.name == "nt":
        account = subprocess.check_output(["whoami"], text=True).strip()
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{account}:(F)", "SYSTEM:(F)"],
            check=True, capture_output=True)
    else:
        path.chmod(0o600)


def connect(config, admin=False):
    return psycopg.connect(host=config["HOST"], port=config["PORT"],
        dbname="postgres" if admin else config["NAME"], user="postgres" if admin else config["USER"],
        password=config["admin_password"] if admin else config["PASSWORD"], connect_timeout=3,
        application_name="nexus-database-preflight")


def wait_ready(config, admin=False):
    for _ in range(30):
        try:
            with connect(config, admin) as connection:
                connection.execute("SELECT 1")
            return
        except psycopg.OperationalError:
            time.sleep(0.5)
    raise RuntimeError("PostgreSQL did not become ready; SQLite fallback is disabled")


def configure_lock_diagnostics(config):
    """Idempotent reload-only setup on our recognized dedicated local instance."""
    metadata = json.loads(docker("inspect", CONTAINER))[0]
    if metadata["Config"].get("Labels", {}).get(LABEL) != "cloud-postgres":
        raise RuntimeError("Refusing to configure an unrecognized database container")
    with connect(config, admin=True) as connection:
        connection.autocommit = True
        for name, value in LOCK_DIAGNOSTICS.items():
            current = connection.execute(sql.SQL("SHOW {}").format(sql.Identifier(name))).fetchone()[0]
            if str(current) != value:
                connection.execute(sql.SQL("ALTER SYSTEM SET {} = {}").format(sql.Identifier(name), sql.Literal(value)))
        connection.execute("SELECT pg_reload_conf()")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["provision", "start", "check"])
    args = parser.parse_args()
    if args.action == "provision":
        if CONFIG.exists() or CONTAINER in docker("ps", "-a", "--format", "{{.Names}}").splitlines() or VOLUME in docker("volume", "ls", "--format", "{{.Name}}").splitlines():
            raise RuntimeError("Refusing to replace existing PostgreSQL configuration/container/volume")
        config = {"NAME": "nexus_cloud", "USER": "nexus_cloud", "PASSWORD": secrets.token_urlsafe(36),
            "HOST": "127.0.0.1", "PORT": 55432, "admin_password": secrets.token_urlsafe(36),
            "container": CONTAINER, "volume": VOLUME, "migration_pending": True}
        private_write(CONFIG, config)
        env = dict(os.environ, POSTGRES_PASSWORD=config["admin_password"])
        docker("run", "-d", "--name", CONTAINER, "--restart", "unless-stopped", "--label", LABEL + "=cloud-postgres",
            "-p", "127.0.0.1:55432:5432", "--mount", f"type=volume,source={VOLUME},target=/var/lib/postgresql/data",
            "-e", "POSTGRES_PASSWORD", "postgres:16-alpine", env=env)
        wait_ready(config, admin=True)
        with connect(config, admin=True) as connection:
            connection.autocommit = True
            connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE").format(
                sql.Identifier(config["USER"]), sql.Literal(config["PASSWORD"])))
            connection.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(config["NAME"]), sql.Identifier(config["USER"])))
    else:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        if args.action == "start":
            metadata = json.loads(docker("inspect", CONTAINER))[0]
            if metadata["Config"].get("Labels", {}).get(LABEL) != "cloud-postgres":
                raise RuntimeError("Refusing to start an unrecognized database container")
            if not metadata["State"]["Running"]:
                docker("start", CONTAINER)
    wait_ready(config)
    if args.action in {"start", "provision"}:
        configure_lock_diagnostics(config)
    with connect(config) as connection:
        result = connection.execute("SELECT current_database(), current_user, version()").fetchone()
    print(json.dumps({"database": result[0], "role": result[1], "engine": "PostgreSQL", "port": config["PORT"], "ready": True}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"PostgreSQL operation failed: {type(error).__name__}; no SQLite fallback.", file=sys.stderr)
        sys.exit(1)
