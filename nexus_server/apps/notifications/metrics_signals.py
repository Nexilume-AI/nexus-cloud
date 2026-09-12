from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender="metrics.AlertEvent")
def alert_event_changed(sender, instance, raw=False, **kwargs):
    if not raw:
        from .sources import record_alert_event
        record_alert_event(instance)


@receiver(post_save, sender="metrics.AlertNotification")
def alert_delivery_changed(sender, instance, raw=False, **kwargs):
    if not raw:
        from .sources import record_delivery
        record_delivery(instance)


@receiver(post_save, sender="metrics.ReportDelivery")
def report_delivery_changed(sender, instance, raw=False, **kwargs):
    if not raw:
        from .sources import record_delivery
        record_delivery(instance, report=True)
