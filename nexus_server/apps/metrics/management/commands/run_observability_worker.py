"""Local supervision only. Production uses independent Celery workers and Beat."""
import time
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections


class Command(BaseCommand):
    help = "Run local monitoring collection/evaluation or notification delivery."

    def add_arguments(self, parser):
        parser.add_argument("--mode", choices=["scheduler", "delivery"], default="scheduler")
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        if settings.NEXUS_PRODUCTION:
            raise CommandError("Production monitoring requires independent Worker/Beat.")
        from apps.metrics.component_host import configured_component
        jobs = configured_component("local").jobs(options["mode"])
        deadlines = {key: 0 for key in jobs}
        try:
            while True:
                for key, (interval, execute) in jobs.items():
                    if time.monotonic() < deadlines[key]:
                        continue
                    close_old_connections()
                    try:
                        execute()
                    except Exception:
                        self.stderr.write(f"Monitoring {key} failed; retry scheduled. Details redacted.")
                    deadlines[key] = time.monotonic() + interval
                if options["once"]:
                    return
                time.sleep(1)
        except KeyboardInterrupt:
            return
