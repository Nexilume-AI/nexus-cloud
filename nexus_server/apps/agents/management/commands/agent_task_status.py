import json
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import Count
from django.utils import timezone
from apps.agents.models import AgentDisplayRun, AgentExecutionTask, AgentRuntimeInvocation, AgentTaskExecution


class Command(BaseCommand):
    help = "Read-only Agent execution health counts; no payloads, credentials or identities."

    def handle(self, *args, **options):
        now = timezone.now()
        active = AgentDisplayRun.objects.filter(run_kind="invocation", status="running")
        legacy_expired = AgentExecutionTask.objects.filter(execution__isnull=True,
            status__in=["working", "input_required"], expires_at__lte=now)
        self.stdout.write(json.dumps({
            "engine": connection.vendor,
            "active_runs": active.count(),
            "legacy_expired_tasks": legacy_expired.count(),
            "pending_invocations": AgentRuntimeInvocation.objects.filter(status="pending").count(),
            "queue": list(AgentTaskExecution.objects.values("state").annotate(count=Count("task_id"))),
            "expired_running_leases": AgentTaskExecution.objects.filter(state="running", lease_expires_at__lte=now).count(),
        }))
