from __future__ import annotations

try:
    from celery import shared_task
except ModuleNotFoundError:
    def shared_task(func):
        return func

from .health import active_deployments_for_health_check, check_deployment_health
from .models import Deployment


@shared_task
def check_one_deployment_health(deployment_id: str) -> dict:
    deployment = Deployment.objects.select_related("tenant", "provider", "provider_account").get(id=deployment_id)
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
    results = {}
    for deployment in active_deployments_for_health_check().iterator():
        result = check_deployment_health(deployment=deployment, write_audit=True)
        checked += 1
        results[str(deployment.id)] = result.status
    return {"checked": checked, "results": results}
