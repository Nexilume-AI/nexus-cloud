from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from apps.agents.edge_presence import expire_edge_node_presence


class Command(BaseCommand):
    help = "Materialize expired OpenWrt Router Presence leases for local deployments."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--interval", type=int, default=60)
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options) -> None:
        interval = max(1, min(int(options["interval"]), 3600))
        while True:
            expired = expire_edge_node_presence()
            if expired:
                self.stdout.write(f"Materialized {expired} expired Router Presence lease(s).")
            if options["once"]:
                return
            time.sleep(interval)
