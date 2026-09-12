from __future__ import annotations

import copy
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework import exceptions, status

from apps.audit.services import write_audit_log
from apps.deployments.models import Deployment
from apps.tenancy.models import Tenant

from .models import Router, RouterRuntimeInvocation, RouterVersion
from .runtime_runner import RouterRuntimeResult, get_router_runtime_runner


class RouterRuntimeError(exceptions.APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Router runtime request failed."
    default_code = "ROUTER_RUNTIME_FAILED"


class RouterRuntimeUnavailable(RouterRuntimeError):
    default_detail = "Router runtime is not available."
    default_code = "ROUTER_RUNTIME_UNAVAILABLE"


class RouterRuntimeInvalidDecision(RouterRuntimeError):
    default_detail = "Router runtime returned an invalid decision."
    default_code = "ROUTER_RUNTIME_INVALID_DECISION"


def select_custom_router_pools(
    *,
    request,
    tenant: Tenant,
    router: Router,
    payload: dict[str, Any],
    pools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    version = deployed_router_version(router=router)
    router_path = router_file_path(version=version)
    candidate_payload = serialize_pool_candidates(pools=pools)
    context = {
        "tenant_id": str(tenant.id),
        "project_id": str(getattr(request, "project_id", "") or ""),
        "router_id": str(router.id),
        "router_version": version.version,
        "request_id": str(getattr(request, "request_id", "") or ""),
        "api_key_id": str(getattr(getattr(request, "api_key", None), "id", "") or ""),
        "strategy": router.strategy,
    }
    result = get_router_runtime_runner().run(
        router_file_path=router_path,
        request_payload=sanitize_payload(payload),
        candidates=candidate_payload,
        context=context,
    )
    if result.error_code:
        log_runtime_invocation(
            request=request,
            tenant=tenant,
            router=router,
            version=version,
            selected_deployment=None,
            result=result,
            status_value=RouterRuntimeInvocation.STATUS_FAILED,
            error_code=result.error_code,
            error_message=result.error_message,
        )
        raise runtime_exception(result=result)
    try:
        selected = validate_pool_decision(decision=result.decision, pools=pools)
    except RouterRuntimeInvalidDecision as exc:
        log_runtime_invocation(
            request=request,
            tenant=tenant,
            router=router,
            version=version,
            selected_deployment=None,
            result=result,
            status_value=RouterRuntimeInvocation.STATUS_FAILED,
            error_code="ROUTER_RUNTIME_INVALID_DECISION",
            error_message=str(exc.detail),
        )
        raise
    decision_reason = ""
    if isinstance(result.decision, dict) and isinstance(result.decision.get("reason"), str):
        decision_reason = result.decision["reason"]
    for pool in selected:
        pool["custom_reason"] = decision_reason
    log_runtime_invocation(
        request=request,
        tenant=tenant,
        router=router,
        version=version,
        selected_deployment=selected[0]["deployments"][0] if selected and selected[0]["deployments"] else None,
        result=result,
        status_value=RouterRuntimeInvocation.STATUS_SUCCESS,
        error_code="",
        error_message="",
    )
    return selected


def deployed_router_version(*, router: Router) -> RouterVersion:
    version = router.versions.filter(version=router.current_version, status=RouterVersion.STATUS_DEPLOYED).first()
    if version is None:
        version = router.versions.filter(status=RouterVersion.STATUS_DEPLOYED).order_by("-created_at").first()
    if version is None:
        raise RouterRuntimeUnavailable("Router has no deployed router.py version.")
    return version


def router_file_path(*, version: RouterVersion) -> Path:
    root = Path(settings.NEXUS_ROUTER_STORAGE_ROOT).resolve()
    path = (root / version.router_file_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RouterRuntimeUnavailable("Router file path is outside router storage root.") from exc
    if not path.exists():
        raise RouterRuntimeUnavailable("Uploaded router.py file is missing.")
    return path


def serialize_pool_candidates(*, pools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    serialized = []
    health_rank = {
        Deployment.HEALTH_HEALTHY: 0,
        Deployment.HEALTH_UNKNOWN: 1,
        Deployment.HEALTH_DEGRADED: 2,
        Deployment.HEALTH_UNHEALTHY: 99,
    }
    for pool in pools:
        group = pool["group"]
        binding = pool["binding"]
        sources = pool["deployments"]
        latencies = [source.last_latency_ms for source in sources if source.last_latency_ms is not None]
        prices = [source.pricing_rate for source in sources]
        best_health = min((source.health_status for source in sources), key=lambda value: health_rank.get(value, 99))
        serialized.append(
            {
                "id": str(group.id),
                "name": group.name,
                "model_group": group.name,
                "routing_strategy": group.routing_strategy,
                "source_count": len(sources),
                "lowest_price_per_1k_tokens": str(min(prices)) if prices else "0",
                "lowest_latency_ms": min(latencies) if latencies else None,
                "best_health_status": best_health,
                "priority": int(binding.priority),
                "weight": int(binding.weight),
                "routing_hint": binding.routing_hint,
            }
        )
    return serialized


def validate_pool_decision(*, decision: dict[str, Any] | None, pools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(decision, dict):
        raise RouterRuntimeInvalidDecision("route() must return an object.")
    requested = decision.get("ordered_pool_ids") or decision.get("pool_ids")
    if requested is None and decision.get("pool_id"):
        requested = [decision["pool_id"]]
    if not isinstance(requested, list) or not requested:
        raise RouterRuntimeInvalidDecision("route() must return non-empty ordered_pool_ids.")
    candidate_by_key: dict[str, dict[str, Any]] = {}
    for pool in pools:
        candidate_by_key[str(pool["id"])] = pool
        candidate_by_key[pool["group"].name] = pool
    selected = []
    seen = set()
    for raw_value in requested:
        value = str(raw_value)
        pool = candidate_by_key.get(value)
        if pool is None:
            raise RouterRuntimeInvalidDecision("route() selected a Model Pool outside router candidates.")
        pool_id = str(pool["id"])
        if pool_id in seen:
            continue
        selected.append(pool)
        seen.add(pool_id)
    if not selected:
        raise RouterRuntimeInvalidDecision("route() did not select a Model Pool.")
    return selected


def sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return redact(copy.deepcopy(payload))


def redact(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if is_secret_key(str(key)):
                result[key] = "[REDACTED]"
            elif str(key).lower() == "url" and isinstance(item, str):
                result[key] = sanitize_multimodal_url(item)
            else:
                result[key] = redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str) and value.startswith("data:"):
        return "[REDACTED_DATA_URL]"
    return value


def sanitize_multimodal_url(value: str) -> str:
    if value.startswith("data:"):
        return "[REDACTED_DATA_URL]"
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if parsed.scheme in {"http", "https"} and parsed.query:
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "[REDACTED_QUERY]", parsed.fragment))
    return value


def is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in ("api_key", "apikey", "secret", "password", "token", "provider_key"))


def runtime_exception(*, result: RouterRuntimeResult) -> RouterRuntimeError:
    if result.error_code in {"NSJAIL_NOT_FOUND", "ROUTER_RUNTIME_DISABLED"}:
        return RouterRuntimeUnavailable(result.error_message or result.error_code)
    return RouterRuntimeError(result.error_message or result.error_code or "Router runtime request failed.")


def log_runtime_invocation(
    *,
    request,
    tenant: Tenant,
    router: Router,
    version: RouterVersion,
    selected_deployment: Deployment | None,
    result: RouterRuntimeResult,
    status_value: str,
    error_code: str,
    error_message: str,
) -> RouterRuntimeInvocation:
    from .integration import router_integration
    return router_integration().log_runtime_invocation(
        request=request, tenant=tenant, router=router, version=version,
        selected_deployment=selected_deployment, result=result, status_value=status_value,
        error_code=error_code, error_message=error_message,
    )


def request_user_or_none(principal):
    user_model = get_user_model()
    return principal if isinstance(principal, user_model) else None
