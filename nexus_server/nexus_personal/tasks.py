"""Personal operational leases; no pricing, settlement or wallet task."""
from celery import shared_task
from apps.common.invocation_lifecycle import release_stale_invocation_reservations
from .configuration import require_personal_distribution


@shared_task(name='nexus_personal.tasks.expire_invocation_leases')
def expire_invocation_leases():
    require_personal_distribution()
    return release_stale_invocation_reservations()
