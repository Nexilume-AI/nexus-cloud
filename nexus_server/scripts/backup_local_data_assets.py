"""Consistent SQLite backup before the local Data Assets rollout.

Does not restore/overwrite anything. Production PostgreSQL/S3 use the DR runbook.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3


def main():
    server = Path(__file__).resolve().parents[1]
    destination = server.parent / ".local/nexus-cloud/backups" / datetime.now(timezone.utc).strftime("data-assets-%Y%m%dT%H%M%SZ")
    destination.mkdir(parents=True, exist_ok=False)
    source = sqlite3.connect(f"file:{(server / 'db.sqlite3').as_posix()}?mode=ro", uri=True)
    try:
        with sqlite3.connect(destination / "db.sqlite3") as target:
            source.backup(target, pages=1000)
            if target.execute("pragma quick_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed")
    finally:
        source.close()
    storage = server / "storage/datasets"
    if storage.exists():
        shutil.copytree(storage, destination / "datasets")
    print(json.dumps({"backup": str(destination), "database_integrity": "ok", "objects": "copied; no deletion"}))


if __name__ == "__main__":
    main()
