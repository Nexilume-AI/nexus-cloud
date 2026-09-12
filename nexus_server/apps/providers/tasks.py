from __future__ import annotations

import secrets
import logging
import time

try:
    from celery import shared_task
except ImportError:  # pragma: no cover
    def shared_task(func):
        return func

from django.core.cache import cache
from django.conf import settings
from django.db.models import F, Max, Q
from django.utils import timezone

from .models import ProviderRuntimeAccount
from .runtime_integration import runtime_integration
from .runtime_services import (
    reconcile_provider_runtime_health,
    reconcile_stale_provider_runtime_starts,
    reconcile_stale_provider_runtime_stops,
    refresh_runtime_model_offers,
    provider_catalog_refresh_result,
)

logger = logging.getLogger(__name__)


def _acquire_maintenance_lock(name: str, *, timeout: int) -> tuple[str, str] | None:
    key = f"nexus:providers:maintenance:{name}"
    token = secrets.token_hex(16)
    return (key, token) if cache.add(key, token, timeout=timeout) else None


def _release_maintenance_lock(lock: tuple[str, str] | None) -> None:
    if lock is None:
        return
    key, token = lock
    if cache.get(key) == token:
        cache.delete(key)


@shared_task
def clear_expired_provider_imports():
    from .import_services import clear_expired_payloads
    return {"cleared": clear_expired_payloads()}


@shared_task
def refresh_active_provider_runtime_models() -> dict[str, int]:
    lock = _acquire_maintenance_lock("model-catalog", timeout=600)
    if lock is None:
        return {"refreshed": 0, "failed": 0, "skipped_overlap": 1}
    refreshed = 0
    failed = 0
    try:
        runtimes = (
            ProviderRuntimeAccount.objects.filter(runtime_integration().maintenance_filter()).filter(status=ProviderRuntimeAccount.STATUS_ACTIVE)
            .annotate(last_catalog_at=Max("model_offers__last_discovered_at"))
            .order_by(F("last_catalog_at").asc(nulls_first=True), "id")
            .iterator()
        )
        for runtime in runtimes:
            state = cache.get(f"nexus:providers:catalog:{runtime.id}")
            if state and state.get("next_at", 0) > time.time():
                continue
            if state is None and runtime.last_catalog_at is not None:
                interval = max(60, int(getattr(settings, "NEXUS_PROVIDER_MODEL_DISCOVERY_INTERVAL_SECONDS", 1800)))
                if (timezone.now() - runtime.last_catalog_at).total_seconds() < interval:
                    continue
            try:
                refresh_runtime_model_offers(runtime=runtime, actor=runtime.owner, strict=True)
                provider_catalog_refresh_result(runtime_id=runtime.id, failed=False)
                refreshed += 1
            except Exception as exc:  # A single Provider must not stop the catalog sweep.
                # No new catalog is not evidence that every known model failed.
                # A successful discovery still removes truly absent models;
                # real runtime/model probes still gate availability separately.
                provider_catalog_refresh_result(runtime_id=runtime.id, failed=True)
                logger.warning(
                    "Provider catalog refresh failed; retry scheduled. runtime_id=%s category=%s",
                    runtime.id, type(exc).__name__,
                )
                failed += 1
        return {"refreshed": refreshed, "failed": failed, "skipped_overlap": 0}
    finally:
        _release_maintenance_lock(lock)


@shared_task
def reconcile_provider_runtime_health_task() -> dict[str, int]:
    # Survive a worker hard-timeout long enough to keep its replacement from
    # overlapping while the terminated process is unwinding.
    lock = _acquire_maintenance_lock("runtime-health", timeout=600)
    if lock is None:
        return {"examined": 0, "healthy": 0, "unavailable": 0, "skipped_overlap": 1}
    try:
        start_recovery = reconcile_stale_provider_runtime_starts()
        stop_recovery = reconcile_stale_provider_runtime_stops()
        runtime_ids = list(
            ProviderRuntimeAccount.objects.filter(runtime_integration().maintenance_filter()).filter(
                Q(status__in=[
                    ProviderRuntimeAccount.STATUS_ACTIVE,
                    ProviderRuntimeAccount.STATUS_UNHEALTHY,
                    ProviderRuntimeAccount.STATUS_LOGIN_REQUIRED,
                ]) | (Q(
                    status=ProviderRuntimeAccount.STATUS_FAILED,
                    last_error__startswith="Automatic start recovery failed:",
                ) & ~Q(encrypted_proxy_api_key=""))
            ).order_by(F("last_health_check_at").asc(nulls_first=True), "id").values_list("id", flat=True)
        )
        healthy = 0
        unavailable = 0
        for runtime_id in runtime_ids:
            try:
                runtime = reconcile_provider_runtime_health(runtime_id=runtime_id)
            except Exception as exc:
                logger.warning("Provider health reconciliation failed. runtime_id=%s category=%s", runtime_id, type(exc).__name__)
                runtime = None
            if runtime and runtime.status == ProviderRuntimeAccount.STATUS_ACTIVE:
                healthy += 1
            else:
                unavailable += 1
        return {
            "examined": len(runtime_ids),
            "healthy": healthy,
            "unavailable": unavailable,
            "stale_starts_recovered": start_recovery["recovered"],
            "stale_start_failures": start_recovery["failed"],
            "stale_stops_recovered": stop_recovery["stopped"],
            "stale_stop_failures": stop_recovery["failed"],
            **runtime_integration().maintenance_summary(),
            "skipped_overlap": 0,
        }
    finally:
        _release_maintenance_lock(lock)
