"""Only operational tasks; financial report scheduling belongs to its distribution."""
from celery import shared_task


@shared_task
def collect_snapshots():
    from .worker_host import configured_workers
    return configured_workers().collect_snapshots()


@shared_task
def evaluate_rules():
    from .worker_host import configured_workers
    return configured_workers().evaluate_rules()


@shared_task
def deliver_notifications():
    from .worker_host import configured_workers
    return configured_workers().deliver_notifications()


@shared_task
def cleanup_monitoring():
    from django.conf import settings
    from .worker_host import configured_workers
    return configured_workers().cleanup_monitoring(dry_run=not getattr(settings, "NEXUS_MONITOR_RETENTION_ENABLED", False))


# Compatibility imports invoke real operational tasks under their own queue
# names. Personal never registers the commercial scheduled-report task.
capture_metric_snapshots_task = collect_snapshots
evaluate_alert_rules_task = evaluate_rules
deliver_monitoring_notifications_task = deliver_notifications
cleanup_monitoring_task = cleanup_monitoring
