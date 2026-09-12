import time
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from apps.agents.docker_lifecycle import reconcile
from apps.common.runtime_health import record_agent_reconciler_heartbeat


class Command(BaseCommand):
    help = "Recover Docker Agent containers on this assigned worker, never replay Agent invocations."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        if settings.NEXUS_AGENT_RUNTIME_RUNNER not in {"docker", "controller"}:
            raise CommandError("This command requires the Docker or Controller Agent runner.")
        while True:
            close_old_connections()
            try:
                count = reconcile()
                record_agent_reconciler_heartbeat(ok=True)
                if count:
                    self.stdout.write(f"Recovered {count} Docker Agent deployments.")
            except Exception:
                record_agent_reconciler_heartbeat(ok=False, error_code="AGENT_RECONCILER_FAILED")
                self.stderr.write("Agent container reconciliation unavailable; retrying.")
            if options["once"]:
                return
            time.sleep(max(1, settings.NEXUS_AGENT_RUNTIME_RECONCILE_SECONDS))
