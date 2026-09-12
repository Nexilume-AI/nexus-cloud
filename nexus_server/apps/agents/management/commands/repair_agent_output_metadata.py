"""Explicit, bounded legacy repair. Never infer the turn of a Chat Run."""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from apps.agents.models import AgentDisplayRun
from apps.agents.services import output_manifest_from_event, sync_output_artifact_from_event


class Command(BaseCommand):
    help = "Preview missing file metadata for one completed legacy Run; --apply opts in. No files or images are read."

    def add_arguments(self, parser):
        parser.add_argument("--run", required=True)
        parser.add_argument("--after-seq", type=int, default=0)
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--apply", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 500 or options["after_seq"] < 0:
            raise CommandError("limit must be 1–500 and after-seq non-negative.")
        run = AgentDisplayRun.objects.select_for_update().filter(pk=options["run"]).first()
        if not run or run.status != "completed" or run.run_kind not in {"legacy", "deployment"} or hasattr(run, "execution_task"):
            raise CommandError("Only completed, single-turn legacy/deployment Runs can be repaired. Invocation history needs explicit turn attribution.")
        events = list(run.events.filter(seq__gt=options["after_seq"], event_type="CUSTOM").order_by("seq")[:options["limit"]])
        repaired = missing = 0
        for event in events:
            manifest = output_manifest_from_event(event=event)
            if not manifest or run.output_artifacts.filter(workspace_path=manifest["workspace_path"]).exists():
                continue
            missing += 1
            if options["apply"]:
                sync_output_artifact_from_event(event=event)
                repaired += 1
        self.stdout.write(f"examined={len(events)} missing={missing} repaired={repaired} next_seq={events[-1].seq if events else options['after_seq']}")
