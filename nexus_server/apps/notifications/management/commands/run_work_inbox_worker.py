from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from apps.notifications.tasks import deliver_work_inbox_push, reconcile_work_inbox


class Command(BaseCommand):
    help = "Run the local Work Inbox reconciler and Web Push dispatcher."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=int, default=15)
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        interval = max(5, min(int(options["interval"]), 60))
        cycle = 0
        while True:
            try:
                if cycle % max(1, 60 // interval) == 0:
                    reconcile_work_inbox.run()
                deliver_work_inbox_push.run()
            except Exception as exc:
                # Keep the standard local worker recoverable after transient DB,
                # Redis, or Push failures without logging source data.
                self.stderr.write(f"Work Inbox cycle failed ({type(exc).__name__}); retrying.")
            cycle += 1
            if options["once"]:
                return
            time.sleep(interval)
