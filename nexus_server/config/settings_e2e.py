"""Isolated SQLite settings used only by the bounded Hyper-V Edge E2E."""

from __future__ import annotations

import os
from pathlib import Path

from .settings import *  # noqa: F403


database_path = os.environ.get("NEXUS_E2E_DATABASE_PATH", "").strip()
if not database_path:
    raise RuntimeError("NEXUS_E2E_DATABASE_PATH is required for settings_e2e")
DATABASES = {  # noqa: F405
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(Path(database_path).resolve()),
        # The bounded Edge E2E deliberately runs the Cloud API, connector
        # renewals, browser polling, and asynchronous Chat turns against one
        # SQLite file. WAL lets readers proceed while the connector writes;
        # the longer busy timeout prevents a transient renewal transaction
        # from failing a caller-owned Run before it reaches OpenWrt.
        "OPTIONS": {
            "timeout": 30,
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL",
            # Avoid WAL BUSY_SNAPSHOT failures when an atomic view reads and
            # then writes while the Edge connector is reconciling. Acquiring
            # the write reservation up front makes SQLite honor busy_timeout.
            "transaction_mode": "IMMEDIATE",
        },
    }
}
