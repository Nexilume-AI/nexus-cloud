"""Recovery for one-shot image operations; never re-dispatch an uncertain POST."""
from celery import shared_task
from apps.common.gateway_lifecycle import expire_image_operations as expire_operations


@shared_task
def expire_image_operations():
    # Keep the deployed task name and scheduler contract unchanged.
    return expire_operations()
