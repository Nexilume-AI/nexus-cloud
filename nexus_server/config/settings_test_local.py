"""Opt-in persistent SQLite test database for slow full-migration suites."""

from __future__ import annotations

import os
from pathlib import Path

from .settings import *  # noqa: F403


test_database = os.environ.get("NEXUS_TEST_DATABASE_PATH", "").strip()
if not test_database:
    raise RuntimeError("NEXUS_TEST_DATABASE_PATH is required for settings_test_local")
DATABASES["default"]["TEST"] = {"NAME": str(Path(test_database).resolve())}  # noqa: F405
