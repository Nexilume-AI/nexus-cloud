from __future__ import annotations

import json
import math
import random
import time
import uuid
from copy import deepcopy
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db.models import F, Q, Sum
from django.utils import timezone
from rest_framework import exceptions, status

from apps.common.invocation_lifecycle import InvocationFundingDenied
from apps.common.gateway_lifecycle import (
    GatewayChargeReservation, estimate_gateway_charge, calculate_cost,
    reserve_gateway_charge, release_gateway_charge_reservation,
    finalize_gateway_success, apply_success_side_effects,
)
from apps.common.models import SoftDeleteModel
from apps.common.project_scope import current_project_id
from apps.deployments.health import mark_deployment_runtime_failure, mark_deployment_runtime_success
from apps.deployments.models import Deployment, ModelGroup, ModelGroupDeployment
from apps.deployments.candidates import (output_has_available_source, model_group_deployment_candidates, resolve_model_source_deployment)
from apps.datasets.media_services import resolve_media_references_for_provider
from apps.providers.models import (
    ProviderRuntimeAccount,
    ProviderRuntimeModelOffer,
)
from apps.providers.quota_services import (
    mark_provider_account_available,
    mark_provider_account_exhausted,
    mark_provider_account_limited,
    provider_account_quota_available,
    provider_account_quota_rank,
)
from apps.providers.runtime_services import (
    ProviderCapacityUnavailable,
    record_failed_pool_usage,
    record_pool_usage,
    reserve_provider_capacity,
)
from apps.routers.models import Router, RouterChildBinding, RouterOutput
from apps.routers.runtime_services import select_custom_router_pools
from apps.tenancy.models import Tenant
from apps.common.request_context import get_tenant_from_request

from .integration import gateway_integration
from .models import GatewayRequestLog
from .capabilities import enforce_operation_policy, filter_candidates, request_operation, ModelOperationUnsupported
from .provider_clients import OpenAICompatibleClient, ProviderClientError, ProviderResponse, ProviderStreamEvent, estimate_prompt_tokens


class GatewayError(exceptions.APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Gateway request failed."
    default_code = "GATEWAY_ERROR"


class ModelNotFound(GatewayError):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "Model not found."
    default_code = "MODEL_NOT_FOUND"


class DeploymentNotAvailable(GatewayError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Deployment is not available."
    default_code = "DEPLOYMENT_NOT_AVAILABLE"


class ProviderAccountMissing(GatewayError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Provider account not found."
    default_code = "PROVIDER_ACCOUNT_NOT_FOUND"


class ProviderRequestFailed(GatewayError):
    status_code = status.HTTP_502_BAD_GATEWAY
    default_detail = "Provider request failed."
    default_code = "PROVIDER_REQUEST_FAILED"


class ProviderUpstreamError(GatewayError):
    status_code = status.HTTP_502_BAD_GATEWAY
    default_detail = "Provider request failed."
    default_code = "PROVIDER_REQUEST_FAILED"

    def __init__(self, detail=None, *, code: str = "PROVIDER_REQUEST_FAILED") -> None:
        self.default_code = code
        super().__init__(detail)


class ModelPolicyDenied(GatewayError):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "API key policy does not allow this model."
    default_code = "MODEL_POLICY_DENIED"


class APIKeyScopeDenied(GatewayError):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "API key scope does not allow gateway model calls."
    default_code = "API_KEY_SCOPE_DENIED"


class RouterPolicyDenied(GatewayError):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "API key policy does not allow this router."
    default_code = "ROUTER_POLICY_DENIED"


class GatewayRequestReplay(GatewayError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "This request ID is already being processed or has already completed."
    default_code = "GATEWAY_REQUEST_REPLAY"


@dataclass
class RoutingResolution:
    deployments: list[Deployment]
    trace: dict[str, Any]


GATEWAY_RESERVATION_REF = "gateway_request_reservation"
GATEWAY_RESERVATION_RELEASE_REF = "gateway_request_reservation_release"


def chat_completions(*, request, payload: dict[str, Any]) -> dict[str, Any]:
    tenant = get_tenant_from_request(request)
    gateway_integration().prepare_request(request=request, tenant=tenant, payload=payload)
    api_key = getattr(request, "api_key", None)
    enforce_operation_policy(api_key, request_operation(payload))
    apply_single_router_key_default(payload=payload, api_key=api_key)
    enforce_api_key_policy(api_key=api_key, model=payload["model"])
    router = get_router_for_payload(tenant=tenant, payload=payload)
    enforce_api_key_router_policy(api_key=api_key, router=router)
    started = time.monotonic()
    deployments = resolve_deployment_candidates(request=request, tenant=tenant, payload=payload)
    provider_payload = provider_request_payload(
        resolve_media_references_for_provider(request=request, payload=payload)
    )
    apply_provider_output_limit(provider_payload)
    try:
        reservation = reserve_gateway_charge(
            request=request,
            tenant=tenant,
            deployments=deployments,
            provider_payload=provider_payload,
        )
    except InvocationFundingDenied:
        write_failure_log(
            request=request,
            tenant=tenant,
            api_key=api_key,
            router=router,
            fallback_count=0,
            deployment=deployments[0] if deployments else None,
            model=payload["model"],
            error_code="BALANCE_NOT_ENOUGH",
            latency_ms=elapsed_ms(started),
            cost=Decimal("0"),
        )
        raise
    last_provider_error: ProviderClientError | None = None
    deployment: Deployment | None = None
    capacity_reservation: Any = None
    capacity_unavailable = False
    try:
        for deployment in deployments:
            try:
                try:
                    capacity_reservation = reserve_provider_capacity_for_deployment(
                        request=request,
                        deployment=deployment,
                        provider_payload=provider_payload,
                    )
                except ProviderCapacityUnavailable:
                    capacity_unavailable = True
                    continue
                response = OpenAICompatibleClient().chat_completions(deployment=deployment, payload=provider_payload)
                mark_provider_capacity_used(
                    reservation=capacity_reservation,
                    actual_tokens=response.total_tokens,
                )
                mark_deployment_runtime_success(deployment=deployment)
                mark_provider_account_available(account=deployment.provider_account, request=request, reason="Runtime request succeeded.")
                cost = calculate_cost(
                    deployment=deployment,
                    total_tokens=response.total_tokens,
                    request_tokens=response.request_tokens,
                    response_tokens=response.response_tokens,
                    cached_input_tokens=response.cached_input_tokens,
                    reasoning_output_tokens=response.reasoning_output_tokens,
                )
                gateway_log = finalize_gateway_success(
                    request=request,
                    tenant=tenant,
                    api_key=api_key,
                    router=router,
                    fallback_count=count_previous_provider_failures(request=request),
                    deployment=deployment,
                    requested_model=payload["model"],
                    provider_response=response,
                    cost=cost,
                    reservation=reservation,
                    capacity_reservation=capacity_reservation,
                )
                reservation = None
                capacity_reservation = None
                attribute_agent_model_usage(
                    request=request,
                    tenant=tenant,
                    provider_response=response,
                    actual_usage=response.raw.get("usage") if isinstance(response.raw.get("usage"), dict) else None,
                    event_id=f"gateway:{gateway_log.id}",
                )
                return response.raw
            except ProviderClientError as exc:
                release_provider_capacity(reservation=capacity_reservation)
                capacity_reservation = None
                last_provider_error = exc
                update_provider_account_quota_from_error(request=request, deployment=deployment, error=exc)
                mark_deployment_runtime_failure(deployment=deployment, reason=str(exc))
                failure_log = write_failure_log(
                    request=request,
                    tenant=tenant,
                    api_key=api_key,
                    router=router,
                    fallback_count=count_previous_provider_failures(request=request),
                    deployment=deployment,
                    model=payload["model"],
                    error_code=exc.error_code,
                    latency_ms=elapsed_ms(started),
                    cost=Decimal("0"),
                )
                record_failed_pool_usage_for_deployment(
                    request=request,
                    tenant=tenant,
                    deployment=deployment,
                    gateway_request_log=failure_log,
                )
                continue
        if last_provider_error is not None:
            raise ProviderUpstreamError(str(last_provider_error), code=last_provider_error.error_code)
        if capacity_unavailable:
            raise DeploymentNotAvailable("Every matching Provider is at its configured capacity limit.")
        raise DeploymentNotAvailable()
    finally:
        if capacity_reservation is not None:
            release_provider_capacity(reservation=capacity_reservation)
        if reservation is not None:
            release_gateway_charge_reservation(reservation=reservation)


def stream_chat_completions(*, request, payload: dict[str, Any]) -> Iterator[ProviderStreamEvent]:
    tenant = get_tenant_from_request(request)
    gateway_integration().prepare_request(request=request, tenant=tenant, payload=payload)
    api_key = getattr(request, "api_key", None)
    enforce_operation_policy(api_key, request_operation(payload))
    apply_single_router_key_default(payload=payload, api_key=api_key)
    enforce_api_key_policy(api_key=api_key, model=payload["model"])
    router = get_router_for_payload(tenant=tenant, payload=payload)
    enforce_api_key_router_policy(api_key=api_key, router=router)
    deployments = resolve_deployment_candidates(request=request, tenant=tenant, payload=payload)
    stream_payload = dict(payload)
    stream_payload["stream"] = True
    provider_payload = provider_request_payload(
        resolve_media_references_for_provider(request=request, payload=stream_payload)
    )
    apply_provider_output_limit(provider_payload)
    try:
        reservation = reserve_gateway_charge(
            request=request,
            tenant=tenant,
            deployments=deployments,
            provider_payload=provider_payload,
        )
    except InvocationFundingDenied:
        write_failure_log(
            request=request,
            tenant=tenant,
            api_key=api_key,
            router=router,
            fallback_count=0,
            deployment=deployments[0] if deployments else None,
            model=payload["model"],
            error_code="BALANCE_NOT_ENOUGH",
            latency_ms=1,
            cost=Decimal("0"),
        )
        yield ProviderStreamEvent(error_code="BALANCE_NOT_ENOUGH", error_message="Available billing funds are not enough.")
        return
    last_provider_error: ProviderClientError | None = None
    capacity_reservation: Any = None
    capacity_unavailable = False
    try:
        for deployment in deployments:
            started = time.monotonic()
            text_parts: list[str] = []
            final_usage: dict[str, Any] = {}
            finish_reason = ""
            first_chunk_sent = False
            try:
                try:
                    capacity_reservation = reserve_provider_capacity_for_deployment(
                        request=request,
                        deployment=deployment,
                        provider_payload=provider_payload,
                    )
                except ProviderCapacityUnavailable:
                    capacity_unavailable = True
                    continue
                for event in OpenAICompatibleClient().stream_chat_completions(deployment=deployment, payload=provider_payload):
                    gateway_integration().observe_stream(request=request, event=event)
                    if event.done:
                        continue
                    if event.raw is not None:
                        first_chunk_sent = True
                    if event.text_delta:
                        text_parts.append(event.text_delta)
                    if event.finish_reason:
                        finish_reason = event.finish_reason
                    if event.usage:
                        final_usage = event.usage
                    yield event
                gateway_integration().complete_stream(request=request)
                provider_response = build_stream_provider_response(
                    deployment=deployment,
                    requested_model=payload["model"],
                    text="".join(text_parts),
                    usage=final_usage,
                    finish_reason=finish_reason,
                    latency_ms=elapsed_ms(started),
                    payload=payload,
                )
                mark_provider_capacity_used(
                    reservation=capacity_reservation,
                    actual_tokens=provider_response.total_tokens,
                )
                mark_deployment_runtime_success(deployment=deployment)
                mark_provider_account_available(account=deployment.provider_account, request=request, reason="Runtime stream request succeeded.")
                cost = calculate_cost(
                    deployment=deployment,
                    total_tokens=provider_response.total_tokens,
                    request_tokens=provider_response.request_tokens,
                    response_tokens=provider_response.response_tokens,
                    cached_input_tokens=provider_response.cached_input_tokens,
                    reasoning_output_tokens=provider_response.reasoning_output_tokens,
                )
                gateway_log = finalize_gateway_success(
                    request=request,
                    tenant=tenant,
                    api_key=api_key,
                    router=router,
                    fallback_count=count_previous_provider_failures(request=request),
                    deployment=deployment,
                    requested_model=payload["model"],
                    provider_response=provider_response,
                    cost=cost,
                    reservation=reservation,
                    capacity_reservation=capacity_reservation,
                )
                reservation = None
                capacity_reservation = None
                attribute_agent_model_usage(
                    request=request,
                    tenant=tenant,
                    provider_response=provider_response,
                    actual_usage=final_usage or None,
                    event_id=f"gateway:{gateway_log.id}",
                )
                return
            except ProviderClientError as exc:
                if first_chunk_sent:
                    mark_provider_capacity_used(
                        reservation=capacity_reservation,
                        actual_tokens=max(estimate_prompt_tokens(payload.get("messages", [])), 1),
                    )
                release_provider_capacity(reservation=capacity_reservation)
                capacity_reservation = None
                last_provider_error = exc
                update_provider_account_quota_from_error(request=request, deployment=deployment, error=exc)
                mark_deployment_runtime_failure(deployment=deployment, reason=str(exc))
                failure_log = write_failure_log(
                    request=request,
                    tenant=tenant,
                    api_key=api_key,
                    router=router,
                    fallback_count=count_previous_provider_failures(request=request),
                    deployment=deployment,
                    model=payload["model"],
                    error_code=exc.error_code,
                    latency_ms=elapsed_ms(started),
                    cost=Decimal("0"),
                )
                record_failed_pool_usage_for_deployment(
                    request=request,
                    tenant=tenant,
                    deployment=deployment,
                    gateway_request_log=failure_log,
                )
                if first_chunk_sent:
                    yield ProviderStreamEvent(error_code=exc.error_code, error_message=str(exc))
                    return
                continue
        if last_provider_error is not None:
            yield ProviderStreamEvent(error_code=last_provider_error.error_code, error_message=str(last_provider_error))
            return
        if capacity_unavailable:
            yield ProviderStreamEvent(
                error_code="PROVIDER_CAPACITY_UNAVAILABLE",
                error_message="Every matching Provider is at its configured capacity limit.",
            )
            return
        yield ProviderStreamEvent(error_code="DEPLOYMENT_NOT_AVAILABLE", error_message="Deployment is not available.")
    finally:
        if capacity_reservation is not None:
            release_provider_capacity(reservation=capacity_reservation)
        if reservation is not None:
            release_gateway_charge_reservation(reservation=reservation)


def resolve_deployment(*, request, tenant: Tenant, payload: dict[str, Any]) -> Deployment:
    return resolve_deployment_candidates(request=request, tenant=tenant, payload=payload)[0]


def resolve_deployment_candidates(*, request, tenant: Tenant, payload: dict[str, Any]) -> list[Deployment]:
    return gateway_integration().resolve_deployment_candidates(request=request, tenant=tenant, payload=payload)


def resolve_router_deployment_candidates(*, request, tenant: Tenant, router_id: str, payload: dict[str, Any]) -> list[Deployment]:
    return resolve_router_routing(request=request, tenant=tenant, router_id=router_id, payload=payload).deployments


def resolve_router_routing(*, request, tenant: Tenant, router_id: str, payload: dict[str, Any]) -> RoutingResolution:
    router = Router.objects.filter(tenant=tenant, id=router_id, status=Router.STATUS_DEPLOYED).first()
    if router is None:
        raise ModelNotFound("Router not found or not deployed.")
    gateway_integration().authorize_router(request=request, tenant=tenant, router=router)
    child_bindings = list(
        router.child_bindings.filter(status=SoftDeleteModel.STATUS_ACTIVE, enabled=True)
        .select_related(
            "child_output",
            "child_output__model_group",
            "child_output__router",
        )
        .order_by("priority", "created_at")
    )
    if router.router_type == Router.TYPE_AGGREGATION:
        return resolve_aggregation_router(
            request=request,
            tenant=tenant,
            router=router,
            payload=payload,
            bindings=child_bindings,
        )

    outputs = list(
        router.outputs.filter(status=SoftDeleteModel.STATUS_ACTIVE, enabled=True)
        .select_related("model_group")
        .order_by("-is_default", "created_at")
    )
    selected_output = None
    if outputs:
        selected_output = next((output for output in outputs if output.model_name == payload["model"]), None)
        if selected_output is None:
            available = ", ".join(output.model_name for output in outputs)
            raise ModelNotFound(f"Model is not exported by this Router. Available models: {available}.")
    return resolve_execution_router(
        request=request,
        tenant=tenant,
        router=router,
        payload=payload,
        output=selected_output,
    )


def resolve_execution_router(
    *,
    request,
    tenant: Tenant,
    router: Router,
    payload: dict[str, Any],
    output: RouterOutput | None,
) -> RoutingResolution:
    bindings = list(
        router.model_group_bindings.filter(status=SoftDeleteModel.STATUS_ACTIVE, enabled=True)
        .select_related("model_group", "model_group__canonical_model")
        .order_by("priority", "created_at")
    )
    if output is not None:
        if output.model_group_id is None or output.model_group is None:
            raise DeploymentNotAvailable("Router output is not connected to an active Model Pool.")
        bindings = [
            binding
            for binding in bindings
            if binding.model_group_id == output.model_group_id
            or (binding.model_group_id is None and binding.model_group_name == output.model_group.name)
        ]
    diagnosed_pools = router_model_group_candidate_pools(
        tenant=tenant,
        router=router,
        bindings=bindings,
        include_excluded=True,
        payload=payload,
    )
    pools = [pool for pool in diagnosed_pools if pool["deployments"]]
    if not pools and any(pool.get("exclusion_reason") == "model_operation_unsupported" for pool in diagnosed_pools):
        raise ModelOperationUnsupported()
    if router.strategy == Router.STRATEGY_CUSTOM:
        pools = select_custom_router_pools(request=request, tenant=tenant, router=router, payload=payload, pools=pools)
    ordered = [deployment for pool in pools for deployment in pool["deployments"]]
    # Each pool has already applied its own Source strategy. Keep pool boundaries
    # intact here so health sorting cannot pull a Source from a later pool ahead
    # of the pool selected by the Router.
    available = dedupe_deployments(ordered)
    if not available:
        if ordered:
            raise DeploymentNotAvailable("Router has no healthy deployment binding.")
        raise DeploymentNotAvailable("Router has no active deployment binding.")
    selected_pool_ids = {str(pool["id"]) for pool in pools}
    selected_pool_ranks = {str(pool["id"]): index + 1 for index, pool in enumerate(pools)}
    custom_reason = ""
    if pools:
        custom_reason = sanitize_custom_router_reason(
            str(pools[0].get("custom_reason") or ""),
            payload=payload,
        )
    trace = build_router_trace(
        router=router,
        pools=diagnosed_pools,
        selected_pool_ids=selected_pool_ids,
        selected_pool_ranks=selected_pool_ranks,
        selected=available[0],
        custom_reason=custom_reason,
    )
    if output is not None:
        trace["output"] = {
            "id": str(output.id),
            "model": output.model_name,
            "router_id": str(router.id),
            "router": router.name,
        }
        trace["path"] = [
            {
                "router_id": str(router.id),
                "router": router.name,
                "type": "execution",
                "model": output.model_name,
            }
        ]
    pool_trace_by_id = {
        str(pool["id"]): {
            "pool_id": str(pool["id"]),
            "pool": (pool["group"].display_name or pool["group"].name) if pool.get("group") else "",
            "strategy": pool["group"].routing_strategy if pool.get("group") else "",
            "candidates": deepcopy(pool.get("source_trace") or []),
        }
        for pool in diagnosed_pools
    }
    for deployment in available:
        deployment._nexus_routing_trace = trace
        deployment._nexus_pool_trace_by_id = pool_trace_by_id
    return RoutingResolution(deployments=available, trace=trace)


def resolve_aggregation_router(
    *,
    request,
    tenant: Tenant,
    router: Router,
    payload: dict[str, Any],
    bindings: list[RouterChildBinding],
) -> RoutingResolution:
    requested_model = payload["model"]
    matching = [binding for binding in bindings if binding.exposed_model_name == requested_model]
    if not matching:
        available = ", ".join(dict.fromkeys(binding.exposed_model_name for binding in bindings))
        raise ModelNotFound(f"Model is not exported by this Aggregation Router. Available models: {available}.")

    candidate_rows: list[dict[str, Any]] = []
    resolved: list[tuple[Deployment, RouterChildBinding, dict[str, Any]]] = []
    for binding in matching:
        output = binding.child_output
        child_router = output.router
        reason = ""
        child_resolution = None
        if child_router_id_invalid(parent=router, child=child_router):
            reason = "router_depth_or_cycle_rejected"
        elif child_router.status != Router.STATUS_DEPLOYED:
            reason = "child_router_not_deployed"
        elif output.status != SoftDeleteModel.STATUS_ACTIVE or not output.enabled or output.model_group_id is None:
            reason = "child_output_unavailable"
        else:
            try:
                child_resolution = resolve_execution_router(
                    request=request,
                    tenant=tenant,
                    router=child_router,
                    payload={**payload, "model": output.model_name},
                    output=output,
                )
            except (DeploymentNotAvailable, ModelNotFound) as exc:
                reason = str(exc.detail if hasattr(exc, "detail") else exc)
        candidate_rows.append(
            {
                "binding_id": str(binding.id),
                "child_output_id": str(output.id),
                "child_router_id": str(child_router.id),
                "child_router": child_router.name,
                "child_model": output.model_name,
                "exposed_model": binding.exposed_model_name,
                "priority": int(binding.priority),
                "weight": int(binding.weight),
                "available": bool(child_resolution and child_resolution.deployments),
                "exclusion_reason": reason,
            }
        )
        if child_resolution:
            for deployment in child_resolution.deployments:
                resolved.append((deployment, binding, child_resolution.trace))

    if not resolved:
        raise DeploymentNotAvailable("Aggregation Router has no available child Router output for this model.")

    deployments = dedupe_deployments([row[0] for row in resolved])
    trace_by_deployment_id: dict[str, dict[str, Any]] = {}
    for deployment, binding, leaf_trace in resolved:
        trace_by_deployment_id.setdefault(
            str(deployment.id),
            build_aggregation_trace(
                router=router,
                requested_model=requested_model,
                selected_binding=binding,
                candidates=candidate_rows,
                leaf_trace=leaf_trace,
            ),
        )
    for deployment in deployments:
        deployment._nexus_routing_trace = trace_by_deployment_id[str(deployment.id)]
    return RoutingResolution(deployments=deployments, trace=trace_by_deployment_id[str(deployments[0].id)])


def child_router_id_invalid(*, parent: Router, child: Router) -> bool:
    if parent.id == child.id:
        return True
    return child.router_type != Router.TYPE_EXECUTION


def build_aggregation_trace(
    *,
    router: Router,
    requested_model: str,
    selected_binding: RouterChildBinding,
    candidates: list[dict[str, Any]],
    leaf_trace: dict[str, Any],
) -> dict[str, Any]:
    selected_output = selected_binding.child_output
    router_candidates = [
        {
            "pool_id": candidate["child_output_id"],
            "pool": f'{candidate["child_router"]} / {candidate["child_model"]}',
            "model": candidate["exposed_model"],
            "rank": index + 1 if candidate["available"] else None,
            "selected": candidate["binding_id"] == str(selected_binding.id),
            "priority": candidate["priority"],
            "weight": candidate["weight"],
            "routing_hint": "child_router",
            "source_count": 1 if candidate["available"] else 0,
            "lowest_price_per_1k_tokens": None,
            "lowest_latency_ms": None,
            "best_health_status": "healthy" if candidate["available"] else "unavailable",
            "exclusion_reason": candidate["exclusion_reason"],
        }
        for index, candidate in enumerate(candidates)
    ]
    return {
        "version": 2,
        "path": [
            {
                "router_id": str(router.id),
                "router": router.name,
                "type": "aggregation",
                "model": requested_model,
            },
            {
                "router_id": str(selected_output.router_id),
                "router": selected_output.router.name,
                "type": "execution",
                "model": selected_output.model_name,
            },
        ],
        "aggregation": {
            "model": requested_model,
            "candidates": deepcopy(candidates),
            "selected_binding_id": str(selected_binding.id),
            "selected_child_router_id": str(selected_output.router_id),
            "selected_child_output_id": str(selected_output.id),
        },
        "router": {
            "strategy": router.strategy,
            "candidates": router_candidates,
            "selected_pool_id": str(selected_output.id),
            "reason": "Selected child Router output by Aggregation Router policy.",
        },
        "pool": deepcopy(leaf_trace.get("pool") or {"strategy": "", "candidates": [], "selected_source_id": ""}),
        "child_router": deepcopy(leaf_trace),
    }


def router_model_catalog(*, tenant: Tenant, router: Router) -> list[dict[str, Any]]:
    """Return stable Router model names and their current end-to-end availability."""
    from .capabilities import group_contract

    child_bindings = list(
        router.child_bindings.filter(status=SoftDeleteModel.STATUS_ACTIVE, enabled=True)
        .select_related("child_output", "child_output__router", "child_output__model_group")
        .order_by("priority", "created_at")
    )
    if router.router_type == Router.TYPE_AGGREGATION:
        rows: dict[str, dict[str, Any]] = {}
        for binding in child_bindings:
            row = rows.setdefault(
                binding.exposed_model_name,
                {
                    "id": binding.exposed_model_name,
                    "available": False,
                    "router_type": Router.TYPE_AGGREGATION,
                    "targets": [],
                    "code": "ROUTER_MODEL_UNAVAILABLE",
                    "message": "No child Router output is currently available.",
                },
            )
            output = binding.child_output
            available = (
                not child_router_id_invalid(parent=router, child=output.router)
                and output.router.status == Router.STATUS_DEPLOYED
                and output.status == SoftDeleteModel.STATUS_ACTIVE
                and output.enabled
                and output.model_group_id is not None
                and output_has_available_source(tenant=tenant, output=output)
            )
            row["targets"].append(
                {
                    "child_router_id": str(output.router_id),
                    "child_router": output.router.name,
                    "child_model": output.model_name,
                    "available": available,
                }
            )
            if available:
                row.update({"available": True, "code": "", "message": ""})
                row["model_contract"] = group_contract(tenant=tenant, group=output.model_group)
        return list(rows.values())

    outputs = list(
        router.outputs.filter(status=SoftDeleteModel.STATUS_ACTIVE, enabled=True)
        .select_related("model_group")
        .order_by("-is_default", "created_at")
    )
    if not outputs:
        for binding in router.model_group_bindings.filter(status=SoftDeleteModel.STATUS_ACTIVE, enabled=True).select_related(
            "model_group"
        ).order_by("priority", "created_at"):
            group = binding.model_group
            if group is None:
                continue
            outputs.append(
                RouterOutput(router=router, model_group=group, model_name=group.name, enabled=True)
            )
    return [
        {
            "id": output.model_name,
            "model_contract": group_contract(tenant=tenant, group=output.model_group),
            "available": output_has_available_source(tenant=tenant, output=output),
            "router_type": Router.TYPE_EXECUTION,
            "targets": [
                {
                    "model_group_id": str(output.model_group_id or ""),
                    "model_group": output.model_group.name if output.model_group else "",
                }
            ],
            "code": "" if output_has_available_source(tenant=tenant, output=output) else "ROUTER_MODEL_UNAVAILABLE",
            "message": "" if output_has_available_source(tenant=tenant, output=output) else "The backing Model Pool has no available Source.",
        }
        for output in outputs
    ]


def router_accessible_model_names(*, tenant: Tenant, router: Router) -> list[str]:
    return [row["id"] for row in router_model_catalog(tenant=tenant, router=router) if row["available"]]











def order_model_group_deployments(*, group: ModelGroup, deployments: list[Deployment]) -> list[Deployment]:
    available = prioritize_healthy_deployments(dedupe_deployments(deployments))
    if not available:
        return []
    if group.routing_strategy == ModelGroup.ROUTING_LOWEST_COST:
        return sorted(available, key=lambda deployment: (deployment.pricing_rate, source_priority(deployment), deployment.created_at))
    if group.routing_strategy == ModelGroup.ROUTING_LOWEST_LATENCY:
        return sorted(available, key=lambda deployment: (deployment.last_latency_ms or 10_000_000, source_priority(deployment), deployment.created_at))
    if group.routing_strategy == ModelGroup.ROUTING_BEST_HEALTH:
        return prioritize_healthy_deployments(available)
    if group.routing_strategy == ModelGroup.ROUTING_WEIGHTED:
        weighted = [
            deployment
            for deployment in available
            if int(getattr(deployment, "_nexus_source_weight", 100) or 0) > 0
        ]
        if not weighted:
            # Existing invalid policies remain callable until an administrator
            # saves them through the revisioned policy validator.
            return sorted(available, key=lambda deployment: (source_priority(deployment), deployment.created_at))
        return sorted(
            weighted,
            key=lambda deployment: (
                -math.log(max(random.random(), 1e-12))
                / int(getattr(deployment, "_nexus_source_weight", 100) or 0),
                source_priority(deployment),
                deployment.created_at,
            ),
        )
    return sorted(
        available,
        key=lambda deployment: (
            int(getattr(deployment, "_nexus_fallback_order", 100) or 100),
            source_priority(deployment),
            int(getattr(deployment, "_nexus_source_weight", 100) or 100),
            deployment.created_at,
        ),
    )


def router_model_group_deployment_candidates(*, tenant: Tenant, router: Router, bindings: list) -> list[Deployment]:
    return [
        deployment
        for pool in router_model_group_candidate_pools(tenant=tenant, router=router, bindings=bindings)
        for deployment in pool["deployments"]
    ]


def router_model_group_candidate_pools(
    *,
    tenant: Tenant,
    router: Router,
    bindings: list,
    include_excluded: bool = False,
    payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    pools: list[dict[str, Any]] = []
    for binding in bindings:
        group = binding.model_group or ModelGroup.objects.select_related("canonical_model").filter(
            tenant=tenant,
            name=binding.model_group_name,
            status=SoftDeleteModel.STATUS_ACTIVE,
        ).first()
        if group is None:
            if include_excluded:
                pools.append(
                    {
                        "id": str(binding.id),
                        "group": None,
                        "binding": binding,
                        "deployments": [],
                        "source_trace": [],
                        "exclusion_reason": "model_pool_not_found",
                    }
                )
            continue
        candidates = model_group_deployment_candidates(
            tenant=tenant,
            group=group,
            provider_name=binding.provider_name if binding.provider_name != "*" else "",
        )
        compatible = filter_candidates(candidates, payload, required=False) if payload else candidates
        ordered_sources = order_model_group_deployments(group=group, deployments=compatible)
        if not ordered_sources:
            if include_excluded:
                pools.append(
                    {
                        "id": str(group.id),
                        "group": group,
                        "binding": binding,
                        "deployments": [],
                        "source_trace": model_group_source_trace(tenant=tenant, group=group, ordered_sources=[]),
                        "exclusion_reason": "model_operation_unsupported" if candidates and not compatible else "no_available_source",
                    }
                )
            continue
        pools.append(
            {
                "id": str(group.id),
                "group": group,
                "binding": binding,
                "deployments": ordered_sources,
                "source_trace": model_group_source_trace(tenant=tenant, group=group, ordered_sources=ordered_sources),
                "exclusion_reason": "",
            }
        )
    return order_router_candidate_pools(router=router, pools=pools, payload=payload or {})


def model_group_source_trace(*, tenant: Tenant, group: ModelGroup, ordered_sources: list[Deployment]) -> list[dict[str, Any]]:
    return gateway_integration().model_group_source_trace(tenant=tenant, group=group, ordered_sources=ordered_sources)


def model_group_source_exclusion_reason(*, tenant: Tenant, link: ModelGroupDeployment) -> str:
    return gateway_integration().model_group_source_exclusion_reason(tenant=tenant, link=link)


def build_router_trace(
    *,
    router: Router,
    pools: list[dict[str, Any]],
    selected_pool_ids: set[str],
    selected_pool_ranks: dict[str, int],
    selected: Deployment,
    custom_reason: str,
) -> dict[str, Any]:
    selected_pool_id = str(getattr(selected, "_nexus_selected_model_group_id", ""))
    selected_source_id = str(getattr(selected, "_nexus_consumer_source_id", selected.id))
    router_candidates: list[dict[str, Any]] = []
    pool_stage: dict[str, Any] = {"strategy": "", "candidates": [], "selected_source_id": selected_source_id}
    available_rank = 0
    for pool in pools:
        group = pool.get("group")
        deployments = pool.get("deployments") or []
        pool_id = str(pool["id"])
        is_custom_excluded = bool(deployments) and router.strategy == Router.STRATEGY_CUSTOM and pool_id not in selected_pool_ids
        if deployments and not is_custom_excluded:
            available_rank += 1
        prices = [deployment.pricing_rate for deployment in deployments]
        latencies = [deployment.last_latency_ms for deployment in deployments if deployment.last_latency_ms]
        health = [deployment.health_status for deployment in deployments]
        exclusion_reason = pool.get("exclusion_reason") or ("custom_router_not_selected" if is_custom_excluded else "")
        source_candidates = deepcopy(pool.get("source_trace") or [])
        if pool_id == selected_pool_id:
            for source in source_candidates:
                source["selected"] = source["source_id"] == selected_source_id
            pool_stage = {
                "pool_id": pool_id,
                "pool": group.display_name or group.name if group else "",
                "canonical_model_key": group.canonical_model.key if group and group.canonical_model_id else "",
                "strategy": group.routing_strategy if group else "",
                "candidates": source_candidates,
                "selected_source_id": selected_source_id,
            }
        router_candidates.append(
            {
                "pool_id": pool_id,
                "pool": (group.display_name or group.name) if group else pool["binding"].model_group_name,
                "model": group.name if group else pool["binding"].model_group_name,
                "canonical_model_key": group.canonical_model.key if group and group.canonical_model_id else "",
                "rank": selected_pool_ranks.get(pool_id, available_rank) if deployments and not is_custom_excluded else None,
                "selected": pool_id == selected_pool_id,
                "priority": int(pool["binding"].priority),
                "weight": int(pool["binding"].weight),
                "routing_hint": pool["binding"].routing_hint,
                "source_count": len(deployments),
                "lowest_price_per_1k_tokens": str(min(prices)) if prices else None,
                "lowest_latency_ms": min(latencies) if latencies else None,
                "best_health_status": best_health_status(health),
                "exclusion_reason": exclusion_reason,
            }
        )
    return {
        "version": 1,
        "router": {
            "strategy": router.strategy,
            "candidates": router_candidates,
            "selected_pool_id": selected_pool_id,
            "reason": custom_reason or "Selected by Router pool policy.",
        },
        "pool": pool_stage,
    }


def best_health_status(values: list[str]) -> str:
    ranks = {
        Deployment.HEALTH_HEALTHY: 0,
        Deployment.HEALTH_UNKNOWN: 1,
        Deployment.HEALTH_DEGRADED: 2,
        Deployment.HEALTH_UNHEALTHY: 99,
    }
    return min(values, key=lambda value: ranks.get(value, 99)) if values else "unavailable"


def sanitize_custom_router_reason(reason: str, *, payload: dict[str, Any]) -> str:
    sanitized = " ".join(reason.split())[:240]
    for message in payload.get("messages", []):
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str) and content:
            sanitized = sanitized.replace(content, "[REDACTED]")
    return sanitized


def order_router_candidate_pools(*, router: Router, pools: list[dict[str, Any]], payload: dict[str, Any]) -> list[dict[str, Any]]:
    def binding_key(pool: dict[str, Any]) -> tuple:
        binding = pool["binding"]
        return (int(binding.priority), -int(binding.weight), binding.created_at)

    def available_key(pool: dict[str, Any]) -> int:
        return 0 if pool.get("deployments") else 1

    if router.strategy in {Router.STRATEGY_COST, Router.STRATEGY_LOWEST_COST_POOL}:
        return sorted(
            pools,
            key=lambda pool: (
                available_key(pool),
                min((deployment.pricing_rate for deployment in pool.get("deployments") or []), default=Decimal("999999999")),
                *binding_key(pool),
            ),
        )
    if router.strategy == Router.STRATEGY_LOWEST_LATENCY_POOL:
        return sorted(
            pools,
            key=lambda pool: (
                available_key(pool),
                min(
                    (deployment.last_latency_ms for deployment in pool.get("deployments") or [] if deployment.last_latency_ms),
                    default=10_000_000,
                ),
                *binding_key(pool),
            ),
        )
    if router.strategy in {Router.STRATEGY_QUALITY, Router.STRATEGY_BEST_HEALTH_POOL}:
        health_score = {
            Deployment.HEALTH_HEALTHY: 0,
            Deployment.HEALTH_UNKNOWN: 1,
            Deployment.HEALTH_DEGRADED: 2,
            Deployment.HEALTH_UNHEALTHY: 99,
        }
        return sorted(
            pools,
            key=lambda pool: (
                available_key(pool),
                min(
                    (health_score.get(deployment.health_status, 99) for deployment in pool.get("deployments") or []),
                    default=99,
                ),
                *binding_key(pool),
            ),
        )
    if router.strategy == Router.STRATEGY_TASK_TYPE:
        requested_task_type = routing_task_type(payload)
        return sorted(
            pools,
            key=lambda pool: (
                available_key(pool),
                0
                if requested_task_type
                and normalize_routing_hint(pool["binding"].routing_hint) == requested_task_type
                else 1,
                *binding_key(pool),
            ),
        )
    return sorted(pools, key=lambda pool: (available_key(pool), *binding_key(pool)))


def source_priority(deployment: Deployment) -> int:
    return int(getattr(deployment, "_nexus_source_priority", 100) or 100)


def routing_task_type(payload: dict[str, Any]) -> str:
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    return normalize_routing_hint(metadata.get("task_type") or metadata.get("nexus_task_type"))


def normalize_routing_hint(value: Any) -> str:
    return str(value or "").strip().casefold()[:128]


def provider_request_payload(payload: dict[str, Any]) -> dict[str, Any]:
    upstream_payload = dict(payload)
    upstream_payload.pop("metadata", None)
    upstream_payload.pop("_nexus_operation", None)
    return upstream_payload


def apply_provider_output_limit(payload: dict[str, Any]) -> None:
    """Ensure preauthorization has a finite output-token ceiling."""

    value = payload.get("max_tokens")
    if isinstance(value, int) and value > 0:
        return
    payload["max_tokens"] = max(int(getattr(settings, "NEXUS_GATEWAY_DEFAULT_MAX_OUTPUT_TOKENS", 512)), 1)


def enforce_api_key_policy(*, api_key: Any | None, model: str) -> None:
    return gateway_integration().enforce_api_key_policy(api_key=api_key, model=model)


def enforce_api_key_router_policy(*, api_key: Any | None, router: Router | None) -> None:
    return gateway_integration().enforce_api_key_router_policy(api_key=api_key, router=router)


def apply_single_router_key_default(*, payload: dict[str, Any], api_key: Any | None) -> None:
    return gateway_integration().apply_single_router_key_default(payload=payload, api_key=api_key)


def request_actor_user(request):
    return request.user if not getattr(request.user, "is_api_key_principal", False) else None


def get_router_for_payload(*, tenant: Tenant, payload: dict[str, Any]) -> Router | None:
    return gateway_integration().get_router_for_payload(tenant=tenant, payload=payload)


def build_stream_provider_response(
    *,
    deployment: Deployment,
    requested_model: str,
    text: str,
    usage: dict[str, Any],
    finish_reason: str,
    latency_ms: int,
    payload: dict[str, Any],
) -> ProviderResponse:
    request_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0) if usage else 0
    response_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0) if usage else 0
    if request_tokens <= 0:
        request_tokens = estimate_prompt_tokens(payload.get("messages", []))
    if response_tokens <= 0:
        response_tokens = max(len(text.split()), 1 if text else 0)
    total_tokens = int(usage.get("total_tokens") or request_tokens + response_tokens) if usage else request_tokens + response_tokens
    input_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {} if usage else {}
    output_details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {} if usage else {}
    cached_input_tokens = min(max(int(input_details.get("cached_tokens") or 0), 0), request_tokens)
    reasoning_output_tokens = min(max(int(output_details.get("reasoning_tokens") or 0), 0), response_tokens)
    raw = {
        "id": f"chatcmpl_{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": deployment.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish_reason or "stop",
            }
        ],
        "usage": {
            "prompt_tokens": request_tokens,
            "completion_tokens": response_tokens,
            "total_tokens": total_tokens,
        },
    }
    return ProviderResponse(
        raw=raw,
        request_tokens=request_tokens,
        response_tokens=response_tokens,
        total_tokens=total_tokens,
        model=str(raw.get("model") or requested_model),
        latency_ms=latency_ms,
        cached_input_tokens=cached_input_tokens,
        reasoning_output_tokens=reasoning_output_tokens,
    )


def attribute_agent_model_usage(
    *, request, tenant: Tenant, provider_response: ProviderResponse,
    actual_usage: dict[str, Any] | None, event_id: str,
) -> None:
    """Materialize only authenticated, provider-reported usage for one Agent Run."""

    if not actual_usage:
        return
    run_id = str(request.headers.get("X-Nexus-Agent-Run-Id") or "").strip()
    token = str(request.headers.get("X-Nexus-Agent-Usage-Token") or "").strip()
    try:
        context_window = int(request.headers.get("X-Nexus-Agent-Context-Window") or 0)
    except (TypeError, ValueError):
        return
    if not run_id or not token or context_window <= 0:
        return
    supplied_event_id = str(request.headers.get("X-Nexus-Agent-Usage-Event-Id") or "").strip()
    try:
        input_tokens = int(actual_usage.get("prompt_tokens") or actual_usage.get("input_tokens") or 0)
        output_tokens = int(actual_usage.get("completion_tokens") or actual_usage.get("output_tokens") or 0)
        input_details = actual_usage.get("prompt_tokens_details") or actual_usage.get("input_tokens_details") or {}
        output_details = actual_usage.get("completion_tokens_details") or actual_usage.get("output_tokens_details") or {}
        cached_tokens = int(input_details.get("cached_tokens") or 0)
        reasoning_tokens = int(output_details.get("reasoning_tokens") or 0)
    except (AttributeError, TypeError, ValueError):
        return
    from apps.agents.runtime_services import report_agent_model_usage
    try:
        report_agent_model_usage(
            run_id=run_id,
            token=token,
            source="gateway",
            tenant_id=str(tenant.id),
            project_id=str(getattr(request, "project_id", "") or ""),
            data={
                "event_id": supplied_event_id or event_id,
                "model": provider_response.model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cached_input_tokens": cached_tokens,
                "reasoning_tokens": reasoning_tokens,
                "context_window": context_window,
                "primary": True,
            },
        )
    except Exception:
        # Attribution must not alter a successful model response or reveal Run validity.
        return


def record_model_group_source_selection(*, deployment: Deployment) -> None:
    link_id = getattr(deployment, "_nexus_model_group_link_id", "")
    if not link_id:
        return
    ModelGroupDeployment.objects.filter(id=link_id).update(
        last_selected_at=timezone.now(),
        selection_count=F("selection_count") + 1,
        updated_at=timezone.now(),
    )


def write_failure_log(
    *,
    request,
    tenant: Tenant,
    api_key: Any | None,
    router: Router | None,
    fallback_count: int,
    deployment: Deployment | None,
    model: str,
    error_code: str,
    latency_ms: int,
    cost: Decimal,
) -> GatewayRequestLog:
    return gateway_integration().write_failure_log(request=request, tenant=tenant, api_key=api_key, router=router, fallback_count=fallback_count, deployment=deployment, model=model, error_code=error_code, latency_ms=latency_ms, cost=cost)


def consumer_source_id(deployment: Deployment | None) -> str | None:
    if deployment is None:
        return None
    return str(getattr(deployment, "_nexus_consumer_source_id", "") or "") or None


def selected_model_group_id(deployment: Deployment | None) -> str | None:
    if deployment is None:
        return None
    return str(getattr(deployment, "_nexus_selected_model_group_id", "") or "") or None


def routing_trace_for_deployment(deployment: Deployment | None) -> dict[str, Any]:
    if deployment is None:
        return {}
    base = getattr(deployment, "_nexus_routing_trace", None)
    if not isinstance(base, dict):
        return {}
    trace = deepcopy(base)
    pool_id = selected_model_group_id(deployment) or ""
    source_id = consumer_source_id(deployment) or str(deployment.id)
    router_stage = trace.get("router") if isinstance(trace.get("router"), dict) else {}
    router_stage["selected_pool_id"] = pool_id
    for candidate in router_stage.get("candidates", []):
        if isinstance(candidate, dict):
            candidate["selected"] = candidate.get("pool_id") == pool_id
    pool_stage = trace.get("pool") if isinstance(trace.get("pool"), dict) else {}
    if pool_stage.get("pool_id") != pool_id:
        pool_stage = deepcopy(getattr(deployment, "_nexus_pool_trace_by_id", {}).get(pool_id) or {})
        pool_stage["selected_source_id"] = source_id
    pool_stage["selected_source_id"] = source_id
    for candidate in pool_stage.get("candidates", []):
        if isinstance(candidate, dict):
            candidate["selected"] = candidate.get("source_id") == source_id
    trace["router"] = router_stage
    trace["pool"] = pool_stage
    return trace


def elapsed_ms(started: float) -> int:
    return max(int((time.monotonic() - started) * 1000), 1)


def is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True


def prioritize_healthy_deployments(deployments: list[Deployment]) -> list[Deployment]:
    available = [
        deployment
        for deployment in deployments
        if deployment.health_status != Deployment.HEALTH_UNHEALTHY and provider_account_quota_available(deployment.provider_account)
    ]
    if not available:
        return []
    priority = {
        Deployment.HEALTH_HEALTHY: 0,
        Deployment.HEALTH_UNKNOWN: 1,
        Deployment.HEALTH_DEGRADED: 2,
    }
    return sorted(available, key=lambda deployment: (priority.get(deployment.health_status, 99), provider_account_quota_rank(deployment.provider_account)))


def dedupe_deployments(deployments: list[Deployment]) -> list[Deployment]:
    ordered = []
    seen = set()
    for deployment in deployments:
        identity = getattr(deployment, "_nexus_pool_contribution_id", "") or str(deployment.id)
        if identity in seen:
            continue
        ordered.append(deployment)
        seen.add(identity)
    return ordered


def pool_deployments_for_model(*, tenant: Tenant, model_name: str) -> list[Deployment]:
    return gateway_integration().pool_deployments_for_model(tenant=tenant, model_name=model_name)


def pool_deployments_for_router(*, tenant: Tenant, router: Router, bindings: list) -> list[Deployment]:
    return gateway_integration().pool_deployments_for_router(tenant=tenant, router=router, bindings=bindings)


def active_pool_contributions():
    return gateway_integration().active_pool_contributions()


def deployments_from_contributions(*, tenant: Tenant, contributions: list[Any]) -> list[Deployment]:
    return gateway_integration().deployments_from_contributions(tenant=tenant, contributions=contributions)


def pool_contribution_has_capacity(*, contribution: Any) -> bool:
    return gateway_integration().pool_contribution_has_capacity(contribution=contribution)


def pool_tokens_since(*, contribution: Any, since) -> int:
    return gateway_integration().pool_tokens_since(contribution=contribution, since=since)


def pool_reserved_tokens_since(*, contribution: Any, since, now=None) -> int:
    return gateway_integration().pool_reserved_tokens_since(contribution=contribution, since=since, now=now)


def provider_capacity_token_cap(*, provider_payload: dict[str, Any]) -> int:
    serialized = json.dumps(provider_payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    input_token_cap = max(len(serialized), estimate_prompt_tokens(provider_payload.get("messages", [])))
    return max(input_token_cap + int(provider_payload.get("max_tokens") or 0), 1)


def reserve_provider_capacity_for_deployment(
    *,
    request,
    deployment: Deployment,
    provider_payload: dict[str, Any],
) -> Any | None:
    return gateway_integration().reserve_provider_capacity_for_deployment(request=request, deployment=deployment, provider_payload=provider_payload)


def pool_contribution_for_deployment(*, deployment: Deployment) -> Any | None:
    return gateway_integration().pool_contribution_for_deployment(deployment=deployment)


def update_provider_account_quota_from_error(*, request, deployment: Deployment, error: ProviderClientError) -> None:
    if error.error_code == "PROVIDER_QUOTA_EXHAUSTED":
        mark_provider_account_exhausted(
            account=deployment.provider_account,
            request=request,
            reason=str(error),
            retry_after_seconds=error.retry_after_seconds,
        )
    elif error.error_code == "PROVIDER_RATE_LIMITED":
        mark_provider_account_limited(
            account=deployment.provider_account,
            request=request,
            reason=str(error),
            retry_after_seconds=error.retry_after_seconds,
        )


def record_success_pool_usage(
    *, request, tenant: Tenant, deployment: Deployment, gateway_request_log: GatewayRequestLog,
    provider_response: ProviderResponse, settlement: dict[str, Any] | None = None,
    capacity_reservation: Any | None = None,
) -> None:
    return gateway_integration().record_success_pool_usage(request=request, tenant=tenant, deployment=deployment, gateway_request_log=gateway_request_log, provider_response=provider_response, settlement=settlement, capacity_reservation=capacity_reservation)


def record_failed_pool_usage_for_deployment(
    *,
    request,
    tenant: Tenant,
    deployment: Deployment,
    gateway_request_log: GatewayRequestLog,
) -> None:
    return gateway_integration().record_failed_pool_usage_for_deployment(request=request, tenant=tenant, deployment=deployment, gateway_request_log=gateway_request_log)


def count_previous_provider_failures(*, request) -> int:
    return gateway_integration().count_previous_provider_failures(request=request)


def mark_provider_capacity_used(**kwargs):
    return gateway_integration().mark_provider_capacity_used(**kwargs)


def release_provider_capacity(**kwargs):
    return gateway_integration().release_provider_capacity(**kwargs)


def __getattr__(name):
    # Preserve legacy Enterprise model exports without importing private schema
    # into a personal host. These are not fallback model implementations.
    if name in {"APIKey", "APIKeyPolicy", "ProviderPoolContribution", "ProviderPoolUsage", "ProviderCapacityReservation"}:
        return getattr(gateway_integration(), name)
    raise AttributeError(name)
