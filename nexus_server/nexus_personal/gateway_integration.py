"""Owner-scoped Gateway selection; no Marketplace capacity or commercial keys."""
from types import SimpleNamespace
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Q
from rest_framework import exceptions
from apps.deployments.models import ModelGroupDeployment
from apps.deployments.candidates import model_group_deployment_candidates, resolve_model_source_deployment
from apps.gateway.capabilities import filter_candidates
from apps.gateway.models import GatewayRequestLog
from apps.routers.models import Router
from .deployment_integration import PersonalDeploymentIntegration
from .resource_catalog import PersonalResourceCatalog
from .router_integration import PersonalRouterIntegration
from .services import installation_context
from .gateway_catalog import openai_model_catalog, claude_model_catalog, response_events
from .router_credentials import prepare_request


def images(**kwargs):
    from .image_execution import images
    return images(**kwargs)


def image_response(**kwargs):
    from .image_execution import image_response
    return image_response(**kwargs)


def _owner_request(tenant):
    owner_id, _, _ = installation_context()
    request = SimpleNamespace(user=get_user_model().objects.get(pk=owner_id), META={}, headers={}, query_params={})
    PersonalResourceCatalog()._context(request, tenant)
    return request


def _context(request, tenant):
    return PersonalResourceCatalog()._context(request, tenant)


def authorize_router(*, request, tenant, router):
    request = request or _owner_request(tenant)
    _context(request, tenant)
    current, _ = PersonalResourceCatalog()._resource(request=request, resource_type="router", obj=router)
    if current.status != Router.STATUS_DEPLOYED:
        raise exceptions.NotFound("Router is not deployed.")
    # Validate names as well as execution targets before generating diagnostics.
    _authorize_pools(request, tenant, current)
    for output in current.outputs.filter(status="active", enabled=True).select_related("model_group"):
        if output.model_group is None:
            raise exceptions.NotFound("Reconnect this Router to a real Model Pool.")
        PersonalDeploymentIntegration().validate_model_group(request=request, group=output.model_group)
    if current.router_type == Router.TYPE_AGGREGATION:
        for binding in current.child_bindings.filter(status="active", enabled=True).select_related("child_output__router"):
            child = binding.child_output.router
            if child.router_type != Router.TYPE_EXECUTION:
                raise exceptions.NotFound("Aggregation target must be an Execution Router.")
            # An offline owned child can remain mapped. It is excluded by the
            # original routing algorithm, not rewritten into a working target.
            PersonalResourceCatalog()._resource(request=request, resource_type="router", obj=child)
            _authorize_pools(request, tenant, child)
            output = binding.child_output
            if output.model_group_id is None:
                raise exceptions.NotFound("Reconnect this Router to a real Model Pool.")
            PersonalDeploymentIntegration().validate_model_group(request=request, group=output.model_group)


def _authorize_pools(request, tenant, router):
    policy = PersonalDeploymentIntegration()
    for binding in router.model_group_bindings.filter(status="active", enabled=True).select_related("model_group"):
        if binding.model_group is None:
            raise exceptions.NotFound("Reconnect this Router to a real Model Pool.")
        policy.validate_model_group(request=request, group=binding.model_group)
    # Source availability is evaluated separately. A dead/deleted Source must
    # not prevent the original fallback algorithm from choosing a healthy one.


def get_router_for_payload(*, tenant, payload):
    from apps.gateway.services import ModelNotFound, is_uuid
    router_id = payload.get("router_id")
    if not router_id:
        return None
    if not isinstance(router_id, str) or not is_uuid(router_id):
        raise ModelNotFound("Router not found or not deployed.")
    request = _owner_request(tenant)
    router = PersonalRouterIntegration().visible_routers(user=request.user, tenant=tenant).filter(
        pk=router_id, status=Router.STATUS_DEPLOYED).first()
    if router is None:
        raise ModelNotFound("Router not found or not deployed.")
    authorize_router(request=request, tenant=tenant, router=router)
    return router


def resolve_deployment_candidates(*, request, tenant, payload):
    from apps.gateway.services import (resolve_router_routing, order_model_group_deployments,
        prioritize_healthy_deployments, ModelNotFound, DeploymentNotAvailable, is_uuid)
    _context(request, tenant)
    prepare_request(request=request, tenant=tenant, payload=payload)
    if payload.get("router_id"):
        get_router_for_payload(tenant=tenant, payload=payload)
        return resolve_router_routing(request=request, tenant=tenant, router_id=payload["router_id"], payload=payload).deployments
    policy = PersonalDeploymentIntegration()
    model_name = payload["model"]
    group = policy.visible_model_groups(user=request.user, tenant=tenant).filter(name=model_name).first()
    if group is not None:
        candidates = model_group_deployment_candidates(tenant=tenant, group=group)
        available = order_model_group_deployments(group=group, deployments=filter_candidates(candidates, payload))
        if not available:
            raise DeploymentNotAvailable("Model Pool has no available Source.")
        return available
    query = Q(deployment_id=model_name)
    if is_uuid(model_name):
        query |= Q(pk=model_name)
    sources = policy.visible_deployments(user=request.user, tenant=tenant).filter(query).order_by("created_at")
    candidates = [current for source in sources
                  if (current := resolve_model_source_deployment(tenant=tenant, source=source)) is not None]
    available = prioritize_healthy_deployments(filter_candidates(candidates, payload))
    if not available:
        raise ModelNotFound()
    return available


def _diagnostic_links(tenant, group):
    request = _owner_request(tenant)
    policy = PersonalDeploymentIntegration()
    policy.validate_model_group(request=request, group=group)
    sources = policy.visible_deployments(user=request.user, tenant=tenant)
    return ModelGroupDeployment.objects.filter(model_group=group, deployment__in=sources,
        deployment__project_id=group.project_id, deployment__canonical_model_id=group.canonical_model_id
    ).exclude(status="deleted").select_related("deployment", "deployment__provider", "deployment__canonical_model",
        "deployment__provider_account").order_by("priority", "fallback_order", "created_at")


def model_group_source_exclusion_reason(*, tenant, link):
    source = link.deployment
    if link.status != "active":
        return "source_link_inactive"
    if not link.enabled:
        return "source_disabled"
    if source.status != "active":
        return "source_inactive"
    if source.health_status == "unhealthy":
        return "source_unhealthy"
    current = resolve_model_source_deployment(tenant=tenant, source=source)
    return "source_unavailable" if current is None else ""


def model_group_source_trace(*, tenant, group, ordered_sources):
    ranks = {str(getattr(source, "_nexus_consumer_source_id", source.pk)): rank + 1
             for rank, source in enumerate(ordered_sources)}
    result = []
    for link in _diagnostic_links(tenant, group):
        source = link.deployment
        rank = ranks.get(str(source.pk))
        result.append({"source_id": str(source.pk), "source": source.deployment_id,
            "provider": source.provider.name, "canonical_model_key": source.canonical_model.key,
            "upstream_model_id": source.upstream_model_id, "source_type": "runtime",
            "rank": rank, "selected": False, "enabled": bool(link.enabled),
            "priority": link.priority, "weight": link.weight, "fallback_order": link.fallback_order,
            "health_status": source.health_status, "quota_available": rank is not None,
            # An upstream cost estimate used for routing, not wallet settlement.
            "price_per_1k_tokens": str(source.pricing_rate), "latency_ms": source.last_latency_ms or 0,
            "exclusion_reason": "" if rank is not None else model_group_source_exclusion_reason(tenant=tenant, link=link)})
    return result


def _reject_commercial_key(api_key):
    if api_key is not None:
        raise exceptions.PermissionDenied("Commercial API keys are not personal Gateway credentials.")


def enforce_api_key_policy(*, api_key, model):
    _reject_commercial_key(api_key)


def enforce_api_key_router_policy(*, api_key, router):
    _reject_commercial_key(api_key)


def apply_single_router_key_default(*, payload, api_key):
    _reject_commercial_key(api_key)


def count_previous_provider_failures(*, request):
    _, tenant_id, _ = installation_context()
    user, _, project_id = _context(request, tenant_id)
    request_id = getattr(request, "request_id", "")
    if not request_id:
        return 0
    return GatewayRequestLog.objects.filter(tenant_id=tenant_id, project_id=project_id, actor=user,
        request_id=request_id, status="failed", error_code__startswith="PROVIDER_").exclude(
            error_code="PROVIDER_ACCOUNT_NOT_FOUND").count()


from .gateway_lifecycle import (write_failure_log, prepare_attempt as reserve_provider_capacity_for_deployment,
    record_attempt_tokens as mark_provider_capacity_used, release_attempt as release_provider_capacity,
    record_failed_attempt as record_failed_pool_usage_for_deployment, observe_stream, complete_stream)


def record_success_pool_usage(**kwargs):
    raise exceptions.ValidationError("Personal execution completes an admitted receipt, not Marketplace usage.")


def _no_marketplace(**kwargs):
    raise exceptions.NotFound("Marketplace capacity is not part of the personal distribution.")


pool_deployments_for_model = _no_marketplace
pool_deployments_for_router = _no_marketplace
active_pool_contributions = _no_marketplace
deployments_from_contributions = _no_marketplace
pool_contribution_has_capacity = _no_marketplace
pool_tokens_since = _no_marketplace
pool_reserved_tokens_since = _no_marketplace
pool_contribution_for_deployment = _no_marketplace
