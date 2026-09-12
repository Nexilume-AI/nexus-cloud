"""Rollout/recovery tool; never run from a GET or modify the source tasks."""
from datetime import timedelta
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from apps.agents.models import AgentDisplayRun, AgentRunInteraction, AgentPythonBuild
from apps.jobs.models import Job
from apps.notifications.models import UserNotification
from apps.notifications.sources import record_run, record_interaction, record_build, record_job


class Command(BaseCommand):
    help = "Recover missing personal action notices. Completed history is opt-in; no task is answered or retried."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=24)
        parser.add_argument("--include-completed", action="store_true")
        parser.add_argument("--prune", action="store_true", help="Remove notification receipts older than 90 days, not source history.")

    def handle(self, *args, **options):
        if not 1 <= options["hours"] <= 90 * 24:
            raise CommandError("hours must be between 1 and 2160.")
        now = timezone.now()
        since = now - timedelta(hours=options["hours"])
        for row in AgentRunInteraction.objects.filter(status="pending", expires_at__gt=now).select_related("run").iterator(chunk_size=200):
            record_interaction(row)
        statuses = ["failed", "completed"] if options["include_completed"] else ["failed"]
        for row in AgentDisplayRun.objects.filter(run_kind="invocation", status__in=statuses, completed_at__gte=since).iterator(chunk_size=200):
            record_run(row)
        statuses = ["failed", "succeeded"] if options["include_completed"] else ["failed"]
        for row in AgentPythonBuild.objects.filter(status__in=statuses, completed_at__gte=since).select_related("agent").iterator(chunk_size=200):
            record_build(row)
        for row in Job.objects.filter(job_type__in=["agents.runtime.deploy", "agents.deploy"], status__in=statuses, completed_at__gte=since).iterator(chunk_size=200):
            record_job(row)
        if options["prune"]:
            UserNotification.objects.filter(created_at__lt=now - timedelta(days=90)).delete()
        self.stdout.write("Personal notifications reconciled. Source tasks and credentials were not modified.")
