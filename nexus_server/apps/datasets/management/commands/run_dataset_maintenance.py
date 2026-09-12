import time
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections


class Command(BaseCommand):
    help = "Local Data Assets cleanup and operational alerts (production uses Beat)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        if settings.NEXUS_PRODUCTION:
            raise CommandError("Production maintenance requires the independent Worker/Beat runtime.")
        from apps.datasets.tasks import cleanup_dataset_transfers
        from apps.metrics.operations import evaluate_rules as evaluate_alert_rules
        try:
            while True:
                close_old_connections()
                try:
                    cleanup_dataset_transfers()
                    evaluate_alert_rules(metric_prefix="data_assets.")
                except Exception:
                    self.stderr.write("Data Assets maintenance failed; will retry. No content or credentials logged.")
                if options["once"]:
                    return
                time.sleep(60)
        except KeyboardInterrupt:
            return
