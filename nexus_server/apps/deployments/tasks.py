from __future__ import annotations

import logging

try:
    from celery import shared_task
except ModuleNotFoundError:
    def shared_task(func):
        return func

from .health import active_deployments_for_health_check, check_deployment_health
from .models import Deployment


@shared_task
def check_one_deployment_health(deployment_id: str) -> dict:
    deployment = active_deployments_for_health_check().filter(id=deployment_id).first()
    if deployment is None:
        return {"deployment_id": deployment_id, "skipped": True}
    result = check_deployment_health(deployment=deployment, write_audit=True)
    return {
        "deployment_id": str(result.deployment.id),
        "status": result.status,
        "reason": result.reason,
        "latency_ms": result.latency_ms,
    }


@shared_task
def check_all_active_deployments() -> dict:
    checked = 0
    errors = 0
    results = {}
    for deployment in active_deployments_for_health_check().iterator():
        try:
            result = check_deployment_health(deployment=deployment, write_audit=True)
        except Exception:
            errors += 1
            logging.getLogger(__name__).warning("Source health check deferred deployment_id=%s", deployment.id)
            continue
        checked += 1
        results[str(deployment.id)] = result.status
    return {"checked": checked, "results": results, "errors": errors}
