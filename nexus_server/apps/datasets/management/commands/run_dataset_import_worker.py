"""Local-development durable worker; production uses the dedicated Celery queue."""
import time
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db import close_old_connections

from apps.datasets.import_jobs import pending_jobs, run_job


class Command(BaseCommand):
    help = "Process the durable Data Assets import queue (local development only)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        if settings.NEXUS_PRODUCTION:
            raise CommandError("Use the dedicated dataset-imports Celery worker in production.")
        try:
            while True:
                close_old_connections()
                from apps.agents.file_transfers import process_one
                process_one()
                for job_id in pending_jobs():
                    run_job(job_id)
                    close_old_connections()
                if options["once"]:
                    return
                time.sleep(2)
        except KeyboardInterrupt:
            return
