"""Real multi-thread ASGI/queue integration database, never a Cloud host."""
from .settings import *
from pathlib import Path
from tempfile import TemporaryDirectory

# The ordinary TestCase suite owns outer rollback transactions. This explicit
# TransactionTestCase integration uses committed records and concurrent ASGI
# threads instead. SQLite shared-cache memory DBs report SQLITE_LOCKED without
# waiting; use a private file DB with the actual busy timeout/IMMEDIATE writes.
_database_directory = TemporaryDirectory(prefix="nexus-personal-runtime-tests-")
DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:",
    "TEST": {"NAME": str(Path(_database_directory.name) / "personal.sqlite3")},
    "OPTIONS": {"timeout": 20, "transaction_mode": "IMMEDIATE"},
}}
