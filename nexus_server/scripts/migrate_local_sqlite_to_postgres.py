"""One-way, offline import into a NEW local PostgreSQL database only.

SQLite is opened read-only as a legacy backup source, never as a Django runtime.
No existing business PostgreSQL database may be overwritten. The source and
object files are not modified. Row counts and canonical content digests must
match in the same transaction before commit; constraints are rechecked.
"""
import argparse
from datetime import date, datetime, time, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from local_postgres import CONFIG, connect


def convert(value, kind, scale, *, json_decoded=False):
    if value is None:
        return None
    if kind == "uuid":
        return uuid.UUID(str(value))
    if kind == "boolean":
        if value not in (0, 1, False, True):
            raise ValueError("Invalid legacy boolean")
        return bool(value)
    if kind == "numeric":
        number = Decimal(str(value))
        return number.quantize(Decimal(1).scaleb(-scale)) if scale is not None else number
    if kind in {"json", "jsonb"}:
        return json.loads(value) if isinstance(value, str) and not json_decoded else value
    if kind.startswith("timestamp"):
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None and kind.endswith("with time zone") else parsed
    if kind == "date":
        return date.fromisoformat(value) if isinstance(value, str) else value
    if kind.startswith("time "):
        return time.fromisoformat(value) if isinstance(value, str) else value
    if kind in {"text", "character varying", "character"}:
        return str(value)
    if kind in {"integer", "bigint", "smallint"}:
        return int(value)
    if kind == "bytea":
        return bytes(value)
    return value


def encode(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (bytes, memoryview)):
        return bytes(value).hex()
    return str(value)


class Digest:
    def __init__(self):
        self.rows = 0
        self.hashes = []

    def add(self, row):
        canonical = json.dumps(row, sort_keys=True, ensure_ascii=True, separators=(",", ":"), default=encode)
        self.hashes.append(hashlib.sha256(canonical.encode()).digest())
        self.rows += 1

    def summary(self):
        digest = hashlib.sha256()
        for value in sorted(self.hashes):
            digest.update(value)
        return {"rows": self.rows, "sha256": digest.hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--allow-new-database", required=True)
    args = parser.parse_args()
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if not config.get("migration_pending") or args.allow_new_database != config["NAME"] or config["NAME"] != "nexus_cloud":
        raise RuntimeError("Only the pending, newly provisioned nexus_cloud database is eligible")
    if args.report.exists():
        raise RuntimeError("Refusing to overwrite an existing migration report")
    source_path = args.source.resolve(strict=True)
    source = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
    if source.execute("PRAGMA quick_check").fetchone()[0] != "ok" or source.execute("PRAGMA foreign_key_check").fetchone():
        raise RuntimeError("Legacy backup integrity/foreign-key check failed")
    tables = sorted(row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
    report = {"engine": "postgresql", "database": config["NAME"], "tables": {}, "passed": False}
    with connect(config) as target:
        with target.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(798224031)")
            cursor.execute("SELECT count(*) FROM tenancy_tenant")
            if cursor.fetchone()[0]:
                raise RuntimeError("Refusing to overwrite a PostgreSQL database containing Organizations")
            cursor.execute("SELECT count(*) FROM auth_user")
            if cursor.fetchone()[0]:
                raise RuntimeError("Refusing to overwrite a PostgreSQL database containing users")
            target_tables = [row[0] for row in cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")]
            if target_tables != tables:
                raise RuntimeError("Source and target table sets differ; apply matching migrations first")
            cursor.execute("SET CONSTRAINTS ALL DEFERRED")
            # Restore-style import: financial insert triggers describe NEW account
            # openings, not historical accounts at their latest balance. Preserve
            # historical mutation rows verbatim and explicitly validate afterwards.
            # Foreign-key triggers stay enabled throughout.
            for table in tables:
                cursor.execute(sql.SQL("ALTER TABLE {} DISABLE TRIGGER USER").format(sql.Identifier(table)))
            cursor.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY").format(sql.SQL(",").join(map(sql.Identifier, tables))))
            for table in tables:
                columns = list(cursor.execute("SELECT column_name,data_type,numeric_scale FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position", (table,)))
                names = [row[0] for row in columns]
                legacy_names = {row[1] for row in source.execute('PRAGMA table_info("' + table.replace('"', '""') + '")')}
                if set(names) != legacy_names:
                    raise RuntimeError("Column mismatch in " + table)
                expected = Digest()
                select = 'SELECT ' + ','.join('"' + name.replace('"', '""') + '"' for name in names) + ' FROM "' + table.replace('"', '""') + '"'
                with cursor.copy(sql.SQL("COPY {} ({}) FROM STDIN").format(sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, names)))) as copy:
                    for legacy in source.execute(select):
                        row = [convert(value, column[1], column[2]) for value, column in zip(legacy, columns)]
                        expected.add(row)
                        copy.write_row([Jsonb(value) if column[1] in {"json", "jsonb"} and value is not None else value for value, column in zip(row, columns)])
                actual = Digest()
                cursor.execute(sql.SQL("SELECT {} FROM {}").format(sql.SQL(",").join(map(sql.Identifier, names)), sql.Identifier(table)))
                while batch := cursor.fetchmany(1000):
                    for row in batch:
                        actual.add([convert(value, column[1], column[2], json_decoded=True) for value, column in zip(row, columns)])
                if actual.summary() != expected.summary():
                    raise RuntimeError("Content verification mismatch in " + table)
                report["tables"][table] = actual.summary()
                print(f"Verified {table}: {actual.rows} rows", flush=True)
                for name in names:
                    sequence = cursor.execute("SELECT pg_get_serial_sequence(%s,%s)", (table, name)).fetchone()[0]
                    if sequence:
                        maximum = cursor.execute(sql.SQL("SELECT MAX({}) FROM {}").format(sql.Identifier(name), sql.Identifier(table))).fetchone()[0]
                        cursor.execute("SELECT setval(%s,%s,%s)", (sequence, maximum or 1, maximum is not None))
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            for table in tables:
                cursor.execute(sql.SQL("ALTER TABLE {} ENABLE TRIGGER USER").format(sql.Identifier(table)))
            disabled = cursor.execute("SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgenabled='D'").fetchone()[0]
            if disabled:
                raise RuntimeError("A database guard trigger remains disabled")
    source.close()
    report["passed"] = True
    report["total_rows"] = sum(row["rows"] for row in report["tables"].values())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    # Keep the launcher blocked pending an independent ledger/application check.
    print(json.dumps({"tables": len(tables), "rows": report["total_rows"], "passed": True,
        "next": "Verify financial invariants and authorize cutover; runtime remains blocked."}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Database errors may embed actual row contents: never print their text.
        code = getattr(error, "sqlstate", None)
        table = getattr(getattr(error, "diag", None), "table_name", None)
        column = getattr(getattr(error, "diag", None), "column_name", None)
        print(json.dumps({"passed": False, "error_type": type(error).__name__, "sqlstate": code,
            "table": table, "column": column, "reason": str(error) if type(error) is RuntimeError else None}), file=sys.stderr)
        sys.exit(1)
