"""Create a new owner-restricted PostgreSQL custom-format backup; no overwrite."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from local_postgres import CONFIG, CONTAINER, SERVER


def main():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if config.get("migration_pending"):
        raise RuntimeError("Cutover is not complete")
    destination = SERVER.parent / ".local/nexus-cloud/backups" / datetime.now(timezone.utc).strftime("postgres-%Y%m%dT%H%M%SZ")
    destination.mkdir(parents=True, exist_ok=False)
    if os.name == "nt":
        account = subprocess.check_output(["whoami"], text=True).strip()
        subprocess.run(["icacls", str(destination), "/inheritance:r", "/grant:r", f"{account}:(OI)(CI)(F)", "SYSTEM:(OI)(CI)(F)"], capture_output=True, check=True)
    else:
        destination.chmod(0o700)
    executable = shutil.which("docker") or r"D:\Docker\Desktop\resources\bin\docker.exe"
    dump = destination / "nexus_cloud.dump"
    with dump.open("xb") as output:
        result = subprocess.run([executable, "exec", "-e", "PGPASSWORD", CONTAINER,
            "pg_dump", "--username", config["USER"], "--dbname", config["NAME"], "--format=custom", "--no-owner", "--no-acl"],
            env=dict(os.environ, PGPASSWORD=config["PASSWORD"]), stdout=output, stderr=subprocess.PIPE, timeout=180)
    if result.returncode:
        raise RuntimeError("PostgreSQL dump failed; partial backup must not be used")
    with dump.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    metadata = {"engine": "postgresql", "database": config["NAME"], "bytes": dump.stat().st_size,
        "sha256": digest, "object_storage": "unchanged; back up separately for coordinated recovery", "passed": True}
    (destination / "manifest.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps({**metadata, "directory": str(destination)}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"passed": False, "error_type": type(error).__name__}), file=sys.stderr)
        sys.exit(1)
