# Local Cloud: PostgreSQL only

All Django processes require PostgreSQL. Missing configuration, an invalid
database URL, or an unavailable server fails closed; SQLite is never selected.
This applies to web, commands, background maintenance and tests.

Configuration precedence:

1. Explicit `DATABASE_URL` (PostgreSQL only; supported TLS options are retained).
2. Explicit `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` / host / port.
3. Owner-restricted `.local/nexus-cloud/postgres.json` at the repository root.

The third option is only the local deployment convenience; production still
requires explicit configuration, Redis, separate Workers/Beat and the existing
production guards. A PostgreSQL cutover alone does not certify the rest of that
topology.

## Dedicated local instance

- Container: `nexus-cloud-postgres`, PostgreSQL 16.
- Persistent named volume: `nexus-cloud-postgres-data`.
- Host endpoint: `127.0.0.1:55432` (not exposed to the LAN).
- Database / runtime role: `nexus_cloud`.
- Runtime role is not a superuser and cannot create roles/databases.
- Random administration and runtime credentials are separate. The local config
  has user-only Windows ACLs; never print or commit it.
- Docker restart policy: `unless-stopped`. The Cloud launcher starts a stopped
  recognized container, checks database connectivity, then starts Nexus.
- QA containers and databases are not business storage.

`python scripts/local_postgres.py provision` creates a NEW instance only and
refuses an existing container, volume or config. It does not silently initialize
or overwrite an existing installation. `start` and `check` reuse it without
changing data. Provisioning marks `migration_pending=true`, so the Cloud launcher
cannot start against a newly created empty database by accident.

## One-time legacy migration

1. Initialize the new PostgreSQL schema with `python manage.py migrate`.
2. Stop tracked Nexus processes with `start-nexus-cloud.ps1 -StopOnly`; verify no
   other writers exist. Back up the stopped SQLite database and storage. Keep
   the original file untouched.
3. Run `scripts/migrate_local_sqlite_to_postgres.py --source BACKUP/db.sqlite3
   --report REPORT.json --allow-new-database nexus_cloud`.
4. Import refuses a target containing users or Organizations. It requires
   matching table/column sets, reads SQLite only in read-only mode, copies all
   tables in one PostgreSQL transaction, resets sequences, verifies every table's
   row count and canonical SHA-256 digest, and forces foreign-key checks.
   Financial USER triggers are suspended only inside this offline restore
   transaction, then re-enabled; historical mutation journals are copied intact.
5. Independently replay financial mutation chains, verify posted-ledger
   invariants, check release/file counts and core permissioned APIs. If any
   discrepancy remains, keep the launcher blocked; do not auto-repair balances.
6. Run `scripts/activate_local_postgres.py --migration-report REPORT.json
   --verification-report VERIFIED.json`. This independently rechecks every table
   digest, financial mutation replay and posted-ledger invariants, then marks the
   protected local config migration complete only if all checks pass. Start
   `start-nexus-cloud.ps1`. Preserve migration evidence and the legacy
   backup. Never implement rollback by silently enabling SQLite again.

## Operations

Run `python manage.py check_postgres` for a content-safe engine/connectivity check.
Run tests against a separate PostgreSQL database with an appropriate QA role;
the restricted business runtime role deliberately cannot create test databases.
Back up PostgreSQL with `pg_dump` and retain matching object storage. The legacy
SQLite backup helper is not a backup of the active PostgreSQL service.
`python scripts/backup_local_postgres.py` creates a new owner-restricted
custom-format dump and checksum under local backups; it never overwrites an
existing backup and does not copy object storage.

## Connection budgets and lock diagnostics

Every new Django PostgreSQL connection receives role-specific budgets from
`NEXUS_PROCESS_ROLE`. These limit database statements and idle transactions, not
the total duration of an Agent run or an SSE response.

| Process role | Lock wait | SQL statement | Idle in transaction |
| --- | --- | --- | --- |
| `web`, `beat` (also the fallback) | 5 seconds | 60 seconds | 120 seconds |
| `worker` | 15 seconds | 300 seconds | 300 seconds |
| `management` | 60 seconds | 1,800 seconds | 600 seconds |

The standard local launcher selects `management` for checks/migrations, `web`
for the API and `worker` for maintenance processes. Production Compose supplies
the corresponding role to each service. For a manual migration or long
maintenance command, explicitly set `NEXUS_PROCESS_ROLE=management` first.

Override individual budgets with `NEXUS_DB_LOCK_TIMEOUT_MS`,
`NEXUS_DB_STATEMENT_TIMEOUT_MS` and `NEXUS_DB_IDLE_TRANSACTION_TIMEOUT_MS`.
Values must be positive integer milliseconds, and lock wait must be shorter
than statement timeout. Invalid configuration fails startup without echoing
the supplied value. A timeout aborts the current transaction; do not blindly
retry a whole operation that may already have performed external side effects.

On the next `local_postgres.py start` or `provision`, the recognized managed
container enables `log_lock_waits`. This uses the separate local administrator
credential and reloads PostgreSQL configuration without a database restart.
`check` remains read-only, and unrecognized containers are refused. Application
roles do not gain administration privileges.

Lock diagnostics also set `log_min_error_statement=panic`, `log_error_verbosity=terse`,
`log_parameter_max_length=0` and `log_parameter_max_length_on_error=0` so SQL
statements, bind values and deadlock-detail SQL are not attached to these
diagnostic messages. This intentionally omits DETAIL/HINT/CONTEXT from server
logs; it is not a general redactor for arbitrary application error messages.
Production Compose applies the same settings. For an externally managed
PostgreSQL server, its administrator must configure them; application connection
options deliberately do not attempt privileged logging changes. Do not enable
separate SQL/payload logging for troubleshooting caller content. See
[PostgreSQL logging settings](https://www.postgresql.org/docs/16/runtime-config-logging.html).

Code and connection budget changes require a coordinated restart of the API
and workers. Existing connections retain their old session budgets until they
are replaced. No schema migration or business-data backfill is required by
these guardrails.
