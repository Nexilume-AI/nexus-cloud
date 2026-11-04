"""Recovery for one-shot image operations; never re-dispatch an uncertain POST."""
from celery import shared_task
from apps.common.gateway_lifecycle import expire_image_operations as expire_operations


@shared_task
def refresh_models_dev():
    import secrets
    from django.core.cache import cache
    from .models_dev import refresh
    lock = "nexus:model-info:refresh"
    token = secrets.token_hex(16)
    if not cache.add(lock, token, timeout=180):
        return {"skipped": True}
    try:
        return refresh()
    except Exception:
        # Public snapshot failures are separate from Provider health. Keep the
        # previous file intact and let the next scheduled refresh try again.
        return {"updated": False, "reason": "catalog_unavailable_previous_snapshot_retained"}
    finally:
        if cache.get(lock) == token:
            cache.delete(lock)


@shared_task
def expire_image_operations():
    # Keep the deployed task name and scheduler contract unchanged.
    return expire_operations()
