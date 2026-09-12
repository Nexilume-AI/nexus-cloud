from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.audit.services import write_audit_log
from apps.common.models import SoftDeleteModel
from apps.gateway.provider_clients import OpenAICompatibleClient

from .models import Deployment, DeploymentHealthCheck


UNHEALTHY_FAILURE_THRESHOLD = 3


@dataclass
class DeploymentHealthResult:
    deployment: Deployment
    status: str
    reason: str
    latency_ms: int


def check_deployment_health(*, deployment: Deployment, actor=None, request=None, write_audit: bool = True) -> DeploymentHealthResult:
    provider_result = OpenAICompatibleClient().check_health(deployment=deployment)
    if provider_result.healthy and provider_result.degraded:
        status = Deployment.HEALTH_DEGRADED
    elif provider_result.healthy:
        status = Deployment.HEALTH_HEALTHY
    else:
        status = Deployment.HEALTH_UNHEALTHY
    return record_deployment_health(
        deployment=deployment,
        status=status,
        reason=provider_result.reason,
        latency_ms=provider_result.latency_ms,
        actor=actor,
        request=request,
        write_audit=write_audit,
        reset_failures=provider_result.healthy,
    )


@transaction.atomic
def record_deployment_health(
    *,
    deployment: Deployment,
    status: str,
    reason: str,
    latency_ms: int,
    actor=None,
    request=None,
    write_audit: bool = True,
    reset_failures: bool | None = None,
) -> DeploymentHealthResult:
    deployment = Deployment.objects.select_for_update().select_related("tenant").get(pk=deployment.pk)
    previous_status = deployment.health_status
    now = timezone.now()
    clean_reason = str(reason or "")[:512]
    if reset_failures is None:
        reset_failures = status in {Deployment.HEALTH_HEALTHY, Deployment.HEALTH_DEGRADED}
    deployment.health_status = status
    deployment.health_reason = clean_reason
    deployment.last_checked_at = now
    deployment.last_latency_ms = max(int(latency_ms or 0), 0)
    if reset_failures:
        deployment.consecutive_failures = 0
        deployment.last_success_at = now
    else:
        deployment.consecutive_failures += 1
        deployment.last_failure_at = now
    deployment.save(
        update_fields=[
            "health_status",
            "health_reason",
            "last_checked_at",
            "last_latency_ms",
            "consecutive_failures",
            "last_success_at",
            "last_failure_at",
            "updated_at",
        ]
    )
    DeploymentHealthCheck.objects.create(
        deployment=deployment,
        tenant=deployment.tenant,
        status=status,
        reason=clean_reason,
        latency_ms=deployment.last_latency_ms,
    )
    if write_audit and previous_status != status and status in {Deployment.HEALTH_HEALTHY, Deployment.HEALTH_UNHEALTHY}:
        write_audit_log(
            request=request,
            actor=actor,
            tenant=deployment.tenant,
            action=f"deployment.health.{status}",
            resource_type="deployment",
            resource_id=deployment.pk,
            before={"health_status": previous_status},
            after={"health_status": status, "reason": clean_reason},
        )
    return DeploymentHealthResult(deployment=deployment, status=status, reason=clean_reason, latency_ms=deployment.last_latency_ms)


def mark_deployment_runtime_success(*, deployment: Deployment) -> None:
    if deployment.health_status != Deployment.HEALTH_HEALTHY or deployment.consecutive_failures:
        record_deployment_health(
            deployment=deployment,
            status=Deployment.HEALTH_HEALTHY,
            reason="Runtime request succeeded.",
            latency_ms=deployment.last_latency_ms,
            write_audit=False,
            reset_failures=True,
        )


def mark_deployment_runtime_failure(*, deployment: Deployment, reason: str) -> None:
    status = Deployment.HEALTH_DEGRADED
    if deployment.consecutive_failures + 1 >= UNHEALTHY_FAILURE_THRESHOLD:
        status = Deployment.HEALTH_UNHEALTHY
    record_deployment_health(
        deployment=deployment,
        status=status,
        reason=reason,
        latency_ms=deployment.last_latency_ms,
        write_audit=False,
        reset_failures=False,
    )


def active_deployments_for_health_check():
    return Deployment.objects.filter(status=SoftDeleteModel.STATUS_ACTIVE).select_related("tenant", "provider", "provider_account")
