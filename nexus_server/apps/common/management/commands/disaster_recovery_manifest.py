from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone


SCHEMA_VERSION = 1
IGNORED_STORAGE_PREFIXES = (".nexus-readiness-", "celerybeat-schedule")


class Command(BaseCommand):
    help = "Capture or verify a content-safe PostgreSQL and shared-storage disaster recovery manifest."

    def add_arguments(self, parser) -> None:
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--output", help="Write a new manifest to this JSON path.")
        mode.add_argument("--verify", help="Verify current state against this JSON manifest.")
        parser.add_argument(
            "--storage-root",
            default=str(getattr(settings, "NEXUS_SHARED_STORAGE_ROOT", "")),
            help="Shared storage root to hash. Defaults to NEXUS_SHARED_STORAGE_ROOT.",
        )
        parser.add_argument("--verification-report", help="Optional JSON output for verification results.")
        parser.add_argument(
            "--exclude-table",
            action="append",
            default=[],
            help="Explicit table to exclude. Repeat for multiple volatile integration tables.",
        )

    def handle(self, *args, **options) -> None:
        if connection.vendor != "postgresql":
            raise CommandError("Disaster recovery manifests require PostgreSQL.")

        storage_root = Path(options["storage_root"]).resolve()
        excluded_tables = {str(value) for value in options["exclude_table"]}
        current = capture_manifest(storage_root=storage_root, excluded_tables=excluded_tables)

        if options.get("output"):
            output_path = Path(options["output"]).resolve()
            _write_json(output_path, current)
            self.stdout.write(
                self.style.SUCCESS(
                    f"DR manifest captured: tables={len(current['database']['tables'])} "
                    f"files={len(current['storage']['files'])} path={output_path}"
                )
            )
            return

        expected_path = Path(options["verify"]).resolve()
        try:
            expected = json.loads(expected_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"Unable to read DR manifest: {type(exc).__name__}") from exc
        result = compare_manifests(expected=expected, current=current)
        report_path_value = options.get("verification_report")
        if report_path_value:
            _write_json(Path(report_path_value).resolve(), result)
        if not result["passed"]:
            preview = "; ".join(result["mismatches"][:10])
            raise CommandError(
                f"Disaster recovery verification failed with {len(result['mismatches'])} mismatch(es): {preview}"
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"DR verification passed: tables={len(current['database']['tables'])} "
                f"files={len(current['storage']['files'])}"
            )
        )


@transaction.atomic
def capture_manifest(*, storage_root: Path, excluded_tables: set[str] | None = None) -> dict[str, Any]:
    # Every digest belongs to one database snapshot, even if this read-only
    # command is pointed at an active PostgreSQL instance.
    with connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cursor.execute("SHOW server_version")
        server_version = str(cursor.fetchone()[0])
        table_names = sorted(
            table.name
            for table in connection.introspection.get_table_list(cursor)
            if table.type in {"t", "p"}
        )
        excluded = excluded_tables or set()
        tables = {
            table_name: _table_digest(cursor=cursor, table_name=table_name)
            for table_name in table_names
            if table_name not in excluded
        }
        leaf_migrations = sorted(
            ".".join(node) for node in MigrationExecutor(connection).loader.graph.leaf_nodes()
        )
        cursor.execute(
            """
            SELECT sequencename, last_value, start_value, increment_by, max_value,
                   min_value, cache_size, cycle
            FROM pg_sequences
            WHERE schemaname = current_schema()
            ORDER BY sequencename
            """
        )
        sequences = {
            str(row[0]): {
                "last_value": row[1],
                "start_value": row[2],
                "increment_by": row[3],
                "max_value": row[4],
                "min_value": row[5],
                "cache_size": row[6],
                "cycle": row[7],
            }
            for row in cursor.fetchall()
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "captured_at": timezone.now().isoformat(),
        "database": {
            "vendor": connection.vendor,
            "server_version": server_version,
            "tables": tables,
            "migration_leaves": leaf_migrations,
            "sequences": sequences,
            "excluded_tables": sorted(excluded_tables or set()),
        },
        "storage": {"files": storage_manifest(storage_root)},
    }


def _table_digest(*, cursor, table_name: str) -> dict[str, Any]:
    quoted_table = connection.ops.quote_name(table_name)
    cursor.execute(
        f"""
        SELECT COUNT(*), md5(COALESCE(string_agg(row_digest, '' ORDER BY row_digest), ''))
        FROM (
            SELECT md5(row_to_json(source_row)::text) AS row_digest
            FROM {quoted_table} AS source_row
        ) AS digested_rows
        """
    )
    count, digest = cursor.fetchone()
    return {"row_count": int(count), "content_digest": str(digest)}


def storage_manifest(storage_root: Path) -> dict[str, dict[str, Any]]:
    if not storage_root.exists():
        return {}
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(storage_root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(storage_root).as_posix()
        if any(part.startswith(IGNORED_STORAGE_PREFIXES) for part in path.parts):
            continue
        if path.is_symlink():
            target = os.readlink(path)
            files[relative] = {
                "kind": "symlink",
                "target_digest": hashlib.sha256(target.encode("utf-8")).hexdigest(),
            }
            continue
        if not path.is_file():
            continue
        files[relative] = {
            "kind": "file",
            "size": path.stat().st_size,
            "sha256": _file_sha256(path),
        }
    return files


def compare_manifests(*, expected: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    mismatches: list[str] = []
    if expected.get("schema_version") != current.get("schema_version"):
        mismatches.append("manifest schema version differs")

    expected_database = expected.get("database") if isinstance(expected.get("database"), dict) else {}
    current_database = current.get("database") if isinstance(current.get("database"), dict) else {}
    if expected_database.get("vendor") != current_database.get("vendor"):
        mismatches.append("database vendor differs")
    if expected_database.get("migration_leaves") != current_database.get("migration_leaves"):
        mismatches.append("migration leaf set differs")
    _compare_mapping(
        label="database sequence",
        expected=expected_database.get("sequences", {}),
        current=current_database.get("sequences", {}),
        mismatches=mismatches,
    )
    _compare_mapping(
        label="database table",
        expected=expected_database.get("tables", {}),
        current=current_database.get("tables", {}),
        mismatches=mismatches,
    )

    expected_storage = expected.get("storage") if isinstance(expected.get("storage"), dict) else {}
    current_storage = current.get("storage") if isinstance(current.get("storage"), dict) else {}
    _compare_mapping(
        label="storage file",
        expected=expected_storage.get("files", {}),
        current=current_storage.get("files", {}),
        mismatches=mismatches,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "verified_at": timezone.now().isoformat(),
        "passed": not mismatches,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "table_count": len(current_database.get("tables", {})),
        "storage_file_count": len(current_storage.get("files", {})),
    }


def _compare_mapping(*, label: str, expected: Any, current: Any, mismatches: list[str]) -> None:
    expected_mapping = expected if isinstance(expected, dict) else {}
    current_mapping = current if isinstance(current, dict) else {}
    for key in sorted(set(expected_mapping) | set(current_mapping)):
        if key not in expected_mapping:
            mismatches.append(f"unexpected {label}: {key}")
        elif key not in current_mapping:
            mismatches.append(f"missing {label}: {key}")
        elif expected_mapping[key] != current_mapping[key]:
            mismatches.append(f"changed {label}: {key}")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(path)
