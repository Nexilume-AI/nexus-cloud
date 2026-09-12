from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from apps.datasets.models import DatasetVersion
from apps.datasets.manifests import store_entries, release_summary


class Command(BaseCommand):
    help = "Incrementally normalize historical immutable manifests. No object bytes are modified."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--tenant", default="")

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 1000:
            raise CommandError("limit must be between 1 and 1000")
        rows = DatasetVersion.objects.all()
        if options["tenant"]:
            rows = rows.filter(tenant_id=options["tenant"])
        # JSON missing-key semantics differ by backend; explicit isnull is needed.
        from django.db.models import Q
        ids = list(rows.filter(Q(snapshot_json__format__isnull=True) | ~Q(snapshot_json__format=2)).values_list("pk", flat=True)[:options["limit"]])
        count = 0
        blocked = []
        for pk in ids:
            with transaction.atomic():
                version = DatasetVersion.objects.select_for_update().get(pk=pk)
                if version.snapshot_json.get("format") == 2:
                    continue
                original = version.snapshot_json
                entries = original.get("files", [])
                if any(not isinstance(row, dict) or not row.get("file_id") for row in entries):
                    blocked.append(str(pk))
                    continue
                store_entries(version, entries)
                version.snapshot_json = {key: value for key, value in original.items() if key != "files"}
                version.snapshot_json["format"] = 2
                version.snapshot_json["summary"] = release_summary(version)
                version.save(update_fields=["snapshot_json"])
                count += 1
        self.stdout.write(f"Normalized {count} immutable manifests; incompatible legacy manifests={len(blocked)}.")
        if blocked:
            raise CommandError("Legacy manifests without file identities were preserved unchanged: " + ", ".join(blocked))
