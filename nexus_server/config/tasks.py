from __future__ import annotations

from celery import shared_task

from apps.common.runtime_health import record_runtime_heartbeat


@shared_task(name="config.tasks.record_production_runtime_heartbeat")
def record_production_runtime_heartbeat() -> dict:
    return record_runtime_heartbeat()
