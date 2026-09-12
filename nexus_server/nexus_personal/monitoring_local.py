"""Explicit local diagnostic jobs; production uses independent workers/Beat."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from apps.metrics.worker_host import configured_workers


def jobs(mode):
    workers = configured_workers()
    if mode == "delivery":
        return {"delivery": (30, workers.deliver_notifications)}
    if mode != "scheduler":
        raise ImproperlyConfigured("Unsupported local monitoring mode.")
    return {
        "collector": (300, workers.collect_snapshots),
        "evaluator": (60, workers.evaluate_rules),
        "retention": (3600, lambda: workers.cleanup_monitoring(
            dry_run=not getattr(settings, "NEXUS_MONITOR_RETENTION_ENABLED", False))),
    }
