from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from apps.providers.tasks import (
    reconcile_provider_runtime_health_task,
    refresh_active_provider_runtime_models,
)


def _refresh_catalogs():
    close_old_connections()
    try:
        return refresh_active_provider_runtime_models()
    finally:
        close_old_connections()


class Command(BaseCommand):
    help = "Continuously reconcile Provider Runtime health and model catalogs for local Cloud."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        health_interval = max(int(getattr(settings, "NEXUS_PROVIDER_HEALTH_INTERVAL_SECONDS", 60)), 5)
        # One catalog lane, separate from health. The shared task only probes
        # catalogs that are due (including retries), even after process restart.
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="provider-catalog")
        try:
            self._run(options=options, executor=executor, health_interval=health_interval)
        finally:
            executor.shutdown(wait=bool(options["once"]), cancel_futures=True)

    def _run(self, *, options, executor, health_interval):
        catalog_future = None
        while True:
            started_at = time.monotonic()
            close_old_connections()
            try:
                health = reconcile_provider_runtime_health_task()
                self.stdout.write(
                    "Provider health: "
                    f"examined={health.get('examined', 0)} "
                    f"healthy={health.get('healthy', 0)} "
                    f"unavailable={health.get('unavailable', 0)} "
                    f"start_recovery={health.get('stale_starts_recovered', 0)} "
                    f"stop_recovery={health.get('stale_stops_recovered', 0)} "
                    f"settlement_pending={health.get('settlement_pending', 0)}"
                )
                self.stdout.flush()
            except Exception:
                self.stderr.write("Provider health maintenance unavailable; retrying on the next interval.")
                self.stderr.flush()
            if catalog_future is not None and catalog_future.done():
                self._report_catalog(catalog_future)
                catalog_future = None
            if catalog_future is None:
                catalog_future = executor.submit(_refresh_catalogs)
            close_old_connections()
            if options["once"]:
                self._report_catalog(catalog_future)
                return
            elapsed = time.monotonic() - started_at
            time.sleep(max(1.0, health_interval - elapsed))

    def _report_catalog(self, future):
        try:
            catalog = future.result()
            self.stdout.write(
                f"Provider catalogs: refreshed={catalog.get('refreshed', 0)} failed={catalog.get('failed', 0)}"
            )
            self.stdout.flush()
        except Exception:
            self.stderr.write("Provider catalog maintenance unavailable; retrying on the next interval.")
            self.stderr.flush()
