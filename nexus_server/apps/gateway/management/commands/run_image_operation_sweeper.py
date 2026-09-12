"""Execute image reservation recovery in the local eager-task deployment."""
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from apps.gateway.tasks import expire_image_operations


class Command(BaseCommand):
    help = "Release abandoned image-operation reservations without re-dispatch."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=int, default=60)
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        interval = max(1, min(int(options["interval"]), 3600))
        while True:
            close_old_connections()
            try:
                result = expire_image_operations()
                self.stdout.write(f"Image operation recovery: expired={result['expired']}.")
            except Exception:
                # No raw exception: database errors can contain sensitive values.
                self.stderr.write("Image operation recovery failed; will retry.")
                if options["once"]:
                    raise
            if options["once"]:
                return
            time.sleep(interval)
