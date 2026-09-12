import json
from django.core.management.base import BaseCommand, CommandError
from apps.datasets.models import DatasetFile, DatasetVersion
from apps.datasets.operations import verify_object


class Command(BaseCommand):
    help = "Read-only bounded storage integrity verification after restore (files and immutable releases)."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True)
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--after", default="")
        parser.add_argument("--release-id", default="", help="Verify immutable version entries instead of current files.")

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 1000:
            raise CommandError("limit must be between 1 and 1000")
        # Deleted collections remain included: acquired release bytes must survive.
        if options["release_id"]:
            version = DatasetVersion.objects.filter(tenant_id=options["tenant"], pk=options["release_id"]).first()
            if not version:
                raise CommandError("Version unavailable in this tenant")
            from apps.datasets.manifests import raw_entries
            from itertools import islice
            entries = raw_entries(version) if version.snapshot_json.get("format") == 2 else sorted(raw_entries(version), key=lambda row: str(row.get("file_id", "")))
            if version.snapshot_json.get("format") != 2 and any(not row.get("file_id") for row in entries):
                self.stdout.write(json.dumps({"checked": 0, "results": [], "next_after": None,
                    "warning_code": "LEGACY_MANIFEST_INCOMPLETE"}))
                raise CommandError("Legacy manifest has missing file identities; verification cannot attest completeness.")
            rows = list(islice((row for row in entries if str(row.get("file_id", "")) > options["after"]), options["limit"] + 1))
        else:
            files = DatasetFile.objects.filter(tenant_id=options["tenant"]).order_by("pk")
            if options["after"]:
                files = files.filter(pk__gt=options["after"])
            rows = [{"file_id": str(file.pk), "storage_backend": file.storage_backend,
                "object_key": file.object_key or file.storage_path, "size_bytes": file.size_bytes, "sha256": file.sha256}
                for file in files[:options["limit"] + 1]]
        more, rows = len(rows) > options["limit"], rows[:options["limit"]]
        results = [{"file_id": str(row["file_id"]), "status": verify_object(backend=row.get("storage_backend") or "local",
            key=row.get("object_key", ""), expected_size=row.get("size_bytes", 0), expected_hash=row.get("sha256", ""))} for row in rows]
        self.stdout.write(json.dumps({"checked": len(results), "results": results,
            "next_after": results[-1]["file_id"] if more else None}))
        if any(row["status"] != "VERIFIED" for row in results):
            raise CommandError("Storage verification failed; see content-safe status codes above.")
