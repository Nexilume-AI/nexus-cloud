from __future__ import annotations

from collections import Counter
from django.db.models import Avg, Count

from apps.common.models import SoftDeleteModel
from apps.common.project_scope import current_project_id
from apps.common.resource_catalog import ownership_payload
from apps.gateway.models import GatewayRequestLog
from apps.providers.models import ProviderRuntimeAccount, ProviderRuntimeModelOffer
from apps.routers.models import Router
from apps.routers.services import list_routers
from apps.tenancy.services import get_tenant_from_request

from .models import Deployment, ModelGroup, ModelGroupDeployment
from .services import list_deployments, list_visible_models
from .integration import deployment_integration


def topology_payload(*, request) -> dict:
    policy = deployment_integration().topology()
    tenant = get_tenant_from_request(request)
    sources = list(
        policy.sources(list_deployments(request=request))
        .select_related(
            "provider",
            "canonical_model",
            "provider_runtime",
            "runtime_model_offer",
        )
        .order_by("deployment_id")
    )
    pools = list(
        list_visible_models(request=request)
        .select_related("canonical_model")
        .prefetch_related(
            "deployment_links__deployment",
            "deployment_links__deployment__provider",
        )
        .order_by("name")
    )
    source_ids = {source.id for source in sources}
    pool_ids = {pool.id for pool in pools}
    routers = list(
        list_routers(request=request).prefetch_related(
            "model_group_bindings",
            "outputs__model_group",
            "child_bindings__child_output__router",
            "child_bindings__child_output__model_group",
        )
    )
    if current_project_id(request):
        routers = [
            router
            for router in routers
            if router_is_relevant_to_project(router=router, visible_pool_ids=pool_ids)
        ]

    runtime_by_id: dict[str, dict] = {}
    own_runtime_queryset = policy.owned_runtimes(request=request, tenant=tenant)
    for runtime in own_runtime_queryset:
        runtime_by_id[str(runtime.id)] = serialize_runtime(
            runtime=runtime,
            ownership="owned",
            visible_source_ids=source_ids,
        )
    for row in policy.additional_runtimes(sources=sources, visible_source_ids=source_ids):
        runtime_by_id[row["id"]] = row

    source_payload = [serialize_source(source=source, visible_pool_ids=pool_ids) for source in sources]
    pool_payload = [serialize_pool(pool=pool, visible_source_ids=source_ids) for pool in pools]
    visible_router_ids = {router.id for router in routers}
    router_payload = [
        serialize_router(
            router=router,
            visible_pool_ids=pool_ids,
            visible_router_ids=visible_router_ids,
            request=request,
        )
        for router in routers
    ]
    health = Counter(item["health_status"] for item in source_payload)
    return {
        "summary": {
            "runtime_count": len(runtime_by_id),
            "source_count": len(source_payload),
            "pool_count": len(pool_payload),
            "router_count": len(router_payload),
            "health": {
                "healthy": health.get(Deployment.HEALTH_HEALTHY, 0),
                "degraded": health.get(Deployment.HEALTH_DEGRADED, 0),
                "unhealthy": health.get(Deployment.HEALTH_UNHEALTHY, 0),
                "unknown": health.get(Deployment.HEALTH_UNKNOWN, 0),
            },
        },
        "runtimes": list(runtime_by_id.values()),
        "sources": source_payload,
        "pools": pool_payload,
        "routers": router_payload,
    }


def serialize_runtime(*, runtime: ProviderRuntimeAccount, ownership: str, visible_source_ids: set) -> dict:
    policy = deployment_integration().topology()
    context = policy.runtime_context(runtime)
    return {
        "id": str(runtime.id),
        "name": runtime.name,
        "runtime_type": runtime.runtime_type,
        "status": runtime.status,
        "ownership": ownership,
        "resource_ownership": ownership_payload(project=runtime.project),
        "publisher": runtime.tenant.name,
        "model_offers": [
            {
                "id": str(offer.id),
                "canonical_model_key": offer.canonical_model.key if offer.canonical_model_id else None,
                "upstream_model_id": offer.upstream_model_id,
                "status": offer.status,
                "health_status": offer.health_status,
                **policy.offer_fields(offer, context),
                "source_ids": [
                    str(value)
                    for value in offer.sources.filter(id__in=visible_source_ids)
                    .exclude(status=SoftDeleteModel.STATUS_DELETED)
                    .values_list("id", flat=True)
                ],
            }
            for offer in runtime.model_offers.exclude(status=SoftDeleteModel.STATUS_DELETED).select_related("canonical_model")
        ],
    }


def serialize_source(*, source: Deployment, visible_pool_ids: set | None = None) -> dict:
    policy = deployment_integration().topology()
    runtime, _ = policy.source_runtime(source)
    links = source.model_group_links.filter(
        status=SoftDeleteModel.STATUS_ACTIVE,
        enabled=True,
        model_group__status=SoftDeleteModel.STATUS_ACTIVE,
    ).select_related("model_group")
    links = policy.source_links(links, visible_pool_ids=visible_pool_ids)
    effective_health, effective_reason = effective_source_health(source)
    return {
        "id": str(source.id),
        "name": source.deployment_id,
        "provider": source.provider.name,
        "canonical_model_key": source.canonical_model.key,
        "upstream_model_id": source.upstream_model_id,
        "model_offer_id": str(source.runtime_model_offer_id) if source.runtime_model_offer_id else None,
        "source_type": "provider_runtime" if runtime else "manual",
        "runtime_id": str(runtime.id) if runtime else None,
        "publisher": source.tenant.name,
        **policy.source_fields(source),
        "price_per_1k_tokens": str(source.pricing_rate),
        "latency_ms": source.last_latency_ms,
        "health_status": effective_health,
        "health_reason": effective_reason,
        "status": source.status,
        "resource_ownership": ownership_payload(project=source.project),
        "pool_ids": [str(link.model_group_id) for link in links],
    }


def serialize_pool(*, pool: ModelGroup, visible_source_ids: set) -> dict:
    policy = deployment_integration().topology()
    links = list(
        policy.pool_links(pool.deployment_links.filter(
            status=SoftDeleteModel.STATUS_ACTIVE,
            deployment_id__in=visible_source_ids,
            deployment__tenant_id=pool.tenant_id,
            deployment__project_id=pool.project_id,
            deployment__canonical_model_id=pool.canonical_model_id,
        ))
        .select_related(
            "deployment",
            "deployment__provider",
            "deployment__provider_runtime",
            "deployment__runtime_model_offer",
        )
        .order_by("priority", "fallback_order", "created_at")
    )
    enabled = [link.deployment for link in links if link.enabled and link.deployment.status == SoftDeleteModel.STATUS_ACTIVE]
    prices = [source.pricing_rate for source in enabled]
    latencies = [source.last_latency_ms for source in enabled if source.last_latency_ms]
    return {
        "id": str(pool.id),
        "name": pool.name,
        "display_name": pool.display_name or pool.name,
        "canonical_model_key": pool.canonical_model.key,
        "routing_strategy": pool.routing_strategy,
        "routing_config": pool.routing_config,
        "visibility": pool.visibility,
        "status": pool.status,
        "source_count": len(links),
        "enabled_source_count": len(enabled),
        "lowest_price_per_1k_tokens": str(min(prices)) if prices else None,
        "lowest_latency_ms": min(latencies) if latencies else None,
        "health_status": aggregate_health([effective_source_health(source)[0] for source in enabled]),
        "resource_ownership": ownership_payload(project=pool.project),
        "sources": [
            {
                "link_id": str(link.id),
                "source_id": str(link.deployment_id),
                "source": link.deployment.deployment_id,
                "provider": link.deployment.provider.name,
                "enabled": link.enabled,
                "priority": link.priority,
                "weight": link.weight,
                "fallback_order": link.fallback_order,
                "health_status": effective_source_health(link.deployment)[0],
                "price_per_1k_tokens": str(link.deployment.pricing_rate),
                "latency_ms": link.deployment.last_latency_ms,
            }
            for link in links
        ],
    }


def router_is_relevant_to_project(*, router: Router, visible_pool_ids: set) -> bool:
    if router.router_type == Router.TYPE_AGGREGATION:
        return router.child_bindings.filter(
            status=SoftDeleteModel.STATUS_ACTIVE,
            enabled=True,
            child_output__status=SoftDeleteModel.STATUS_ACTIVE,
            child_output__enabled=True,
            child_output__model_group_id__in=visible_pool_ids,
        ).exists()
    return router.model_group_bindings.filter(
        status=SoftDeleteModel.STATUS_ACTIVE,
        enabled=True,
        model_group_id__in=visible_pool_ids,
    ).exists() or router.outputs.filter(
        status=SoftDeleteModel.STATUS_ACTIVE,
        enabled=True,
        model_group_id__in=visible_pool_ids,
    ).exists()


def serialize_router(*, router, visible_pool_ids: set, visible_router_ids: set, request) -> dict:
    policy = deployment_integration().topology()
    bindings = list(
        router.model_group_bindings.filter(
            status=SoftDeleteModel.STATUS_ACTIVE,
            enabled=True,
            model_group_id__in=visible_pool_ids,
        )
        .select_related("model_group", "model_group__canonical_model")
        .order_by("priority", "created_at")
    )
    logs = GatewayRequestLog.objects.filter(tenant=router.tenant, router=router)
    project_id = current_project_id(request)
    if project_id:
        logs = logs.filter(project_id=project_id)
    metrics = logs.aggregate(
        request_count=Count("id"),
        average_latency_ms=Avg("latency_ms"),
        **policy.metric_aggregations(),
    )
    recent = logs.order_by("-created_at").first()
    api_models = serialize_aggregation_models(
        router=router,
        visible_pool_ids=visible_pool_ids,
        visible_router_ids=visible_router_ids,
        project_scoped=bool(current_project_id(request)),
    )
    return {
        "id": str(router.id),
        "name": router.name,
        "router_type": router.router_type,
        "strategy": router.strategy,
        "status": router.status,
        "resource_ownership": ownership_payload(project=router.project),
        "current_version": router.current_version,
        "pool_count": len(bindings),
        "pool_ids": [str(binding.model_group_id) for binding in bindings],
        "pools": [
            {
                "binding_id": str(binding.id),
                "pool_id": str(binding.model_group_id),
                "pool": binding.model_group.display_name or binding.model_group.name,
                "canonical_model_key": binding.model_group.canonical_model.key,
                "priority": binding.priority,
                "weight": binding.weight,
                "routing_hint": binding.routing_hint,
            }
            for binding in bindings
        ],
        "api_models": api_models,
        "execution_router_count": len({item["execution_router_id"] for item in api_models if item["execution_router_id"]}),
        "request_count": metrics["request_count"] or 0,
        "average_latency_ms": round(float(metrics["average_latency_ms"] or 0)),
        **policy.metric_fields(metrics),
        "last_request_at": recent.created_at if recent else None,
        "last_selected_pool_id": str(recent.selected_model_group_id) if recent and recent.selected_model_group_id else None,
        "last_selected_source_id": str(recent.consumer_source_id) if recent and recent.consumer_source_id else None,
    }


def serialize_aggregation_models(
    *,
    router: Router,
    visible_pool_ids: set,
    visible_router_ids: set,
    project_scoped: bool,
) -> list[dict]:
    if router.router_type != Router.TYPE_AGGREGATION:
        return []

    from apps.gateway.services import router_model_catalog

    catalog = {row["id"]: row for row in router_model_catalog(tenant=router.tenant, router=router)}
    bindings = router.child_bindings.filter(
        status=SoftDeleteModel.STATUS_ACTIVE,
        enabled=True,
        child_output__status=SoftDeleteModel.STATUS_ACTIVE,
        child_output__enabled=True,
    ).select_related("child_output", "child_output__router", "child_output__model_group").order_by(
        "priority", "created_at"
    )
    if project_scoped:
        bindings = bindings.filter(child_output__model_group_id__in=visible_pool_ids)

    result: list[dict] = []
    for binding in bindings:
        output = binding.child_output
        child_visible = output.router_id in visible_router_ids
        catalog_row = catalog.get(binding.exposed_model_name, {})
        target = next(
            (
                item
                for item in catalog_row.get("targets", [])
                if item.get("child_router_id") == str(output.router_id)
            ),
            {},
        )
        available = bool(target.get("available"))
        result.append(
            {
                "binding_id": str(binding.id),
                "model_name": binding.exposed_model_name,
                "available": available,
                "code": "" if available else catalog_row.get("code") or "ROUTER_MODEL_UNAVAILABLE",
                "message": "" if available else catalog_row.get("message") or "The mapped Execution Router is unavailable.",
                "execution_router_id": str(output.router_id) if child_visible else "",
                "execution_router_name": output.router.name if child_visible else "Restricted Execution Router",
                "execution_router_status": output.router.status if child_visible else "restricted",
                "execution_model_name": output.model_name if child_visible else "",
            }
        )
    return result


def effective_source_health(source: Deployment) -> tuple[str, str]:
    runtime, offer = deployment_integration().topology().source_runtime(source)
    if runtime is not None and runtime.status != ProviderRuntimeAccount.STATUS_ACTIVE:
        return Deployment.HEALTH_UNHEALTHY, "Provider Runtime is offline."
    if offer is not None:
        if offer.status != ProviderRuntimeModelOffer.STATUS_CONFIRMED:
            return Deployment.HEALTH_UNHEALTHY, "Runtime Model Offer is not confirmed."
        if offer.health_status == ProviderRuntimeModelOffer.HEALTH_UNHEALTHY:
            return Deployment.HEALTH_UNHEALTHY, offer.health_reason or "Runtime Model Offer health check failed."
        if offer.health_status == ProviderRuntimeModelOffer.HEALTH_DEGRADED:
            return Deployment.HEALTH_DEGRADED, offer.health_reason
    return source.health_status, source.health_reason


def aggregate_health(values: list[str]) -> str:
    if not values:
        return "unavailable"
    values = set(values)
    if Deployment.HEALTH_HEALTHY in values:
        return Deployment.HEALTH_HEALTHY
    if Deployment.HEALTH_UNKNOWN in values:
        return Deployment.HEALTH_UNKNOWN
    if Deployment.HEALTH_DEGRADED in values:
        return Deployment.HEALTH_DEGRADED
    return Deployment.HEALTH_UNHEALTHY
