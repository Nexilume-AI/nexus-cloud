"""Owner-bound operational traces, without settlement or arbitrary payload JSON."""
from django.db.models import Q
from types import SimpleNamespace
from rest_framework import exceptions
from apps.common.catalog_pagination import page
from apps.gateway.models import GatewayRequestLog
from apps.routers.services import get_router
from .deployment_integration import PersonalDeploymentIntegration
from .resource_catalog import PersonalResourceCatalog


def _logs(request, router):
    user, tenant_id, project_id = PersonalResourceCatalog()._context(request, router.tenant_id)
    return GatewayRequestLog.objects.filter(tenant_id=tenant_id, router=router, actor=user).filter(
        Q(project_id=project_id) | Q(project_id=""))


def list_router_traces(*, request, router_id, limit=20, cursor=""):
    router = get_router(request=request, router_id=router_id)
    try:
        if not 1 <= int(limit) <= 100:
            raise ValueError()
    except (ValueError, TypeError):
        raise exceptions.ValidationError("limit must be an integer between 1 and 100.") from None
    params = request.query_params.copy()
    params["limit"] = str(int(limit))
    if cursor:
        params["cursor"] = cursor
    paging_request = SimpleNamespace(query_params=params, path=request.path, user=request.user,
        headers=request.headers, tenant_id=getattr(request, "tenant_id", ""),
        project_id=getattr(request, "project_id", ""))
    return page(request=paging_request, queryset=_logs(request, router),
                serialize=lambda rows: _serialize_rows(request, router, rows))


def serialize_router_trace(*, log, request=None):
    # Direct callers also need current ownership, not an untrusted model object.
    if request is None:
        raise exceptions.NotAuthenticated("Trace serialization requires the owner request.")
    router = get_router(request=request, router_id=str(log.router_id))
    current = _logs(request, router).filter(pk=log.pk).first()
    if current is None:
        raise exceptions.NotFound("Trace not found.")
    return _serialize_rows(request, router, [current])[0]


def _references(value, result, depth=0):
    if depth > 6:
        return
    if isinstance(value, dict):
        for key, item in list(value.items())[:64]:
            if key in result and isinstance(item, str) and len(item) <= 64:
                result[key].add(item)
            elif isinstance(item, (dict, list)):
                _references(item, result, depth + 1)
    elif isinstance(value, list):
        for item in value[:100]:
            _references(item, result, depth + 1)


def _serialize_rows(request, router, rows):
    from uuid import UUID
    refs = {key: set() for key in ("pool_id", "source_id")}
    for log in rows:
        for key, values in (("pool_id", (log.selected_model_group_id,)),
                            ("source_id", (log.consumer_source_id, log.deployment_id))):
            refs[key].update(str(value) for value in values if value)
        _references(log.routing_trace, refs)
    # Invalid historical identifiers must neither break rendering nor reach SQL.
    for key in refs:
        valid = set()
        for value in refs[key]:
            try:
                valid.add(UUID(value))
            except (ValueError, TypeError):
                pass
        refs[key] = valid
    policy = PersonalDeploymentIntegration()
    groups = {str(g.pk): g for g in policy.visible_model_groups(user=request.user, tenant=router.tenant_id).filter(pk__in=refs["pool_id"])}
    sources = {str(s.pk): s for s in policy.visible_deployments(user=request.user, tenant=router.tenant_id).filter(pk__in=refs["source_id"])}
    result = []
    for log in rows:
        group = groups.get(str(log.selected_model_group_id))
        source = sources.get(str(log.consumer_source_id))
        deployment = sources.get(str(log.deployment_id))
        result.append({
            "id": str(log.pk), "request_id": log.request_id, "status": log.status,
            "error_code": log.error_code, "model": log.model, "operation": log.operation,
            "latency_ms": log.latency_ms, "fallback_count": log.fallback_count,
            "request_tokens": log.request_tokens, "response_tokens": log.response_tokens,
            "total_tokens": log.total_tokens, "image_count": log.image_count,
            "selected_pool_id": str(group.pk) if group else None,
            "selected_pool": (group.display_name or group.name) if group else None,
            "selected_source_id": str(source.pk) if source else None,
            "selected_source": source.deployment_id if source else None,
            "provider_deployment": deployment.deployment_id if deployment else None,
            "provider": deployment.provider.name if deployment and deployment.provider else None,
            "trace": _trace(log.routing_trace, groups, sources), "created_at": log.created_at,
        })
    return result


def _trace(raw, groups, sources):
    if not isinstance(raw, dict):
        return None
    result = {"version": 1}
    for stage_name, id_field, objects in (("router", "pool_id", groups), ("pool", "source_id", sources)):
        stage = raw.get(stage_name)
        if not isinstance(stage, dict):
            continue
        selected_key = "selected_" + id_field
        selected = stage.get(selected_key)
        strategy = stage.get("strategy")
        stage_result = {"candidates": [], selected_key: selected if isinstance(selected, str) and selected in objects else None}
        if isinstance(strategy, str) and strategy in {"cost", "quality", "custom", "manual_priority", "lowest_cost_pool", "lowest_latency_pool", "best_health_pool",
                        "task_type", "fallback", "weighted", "lowest_cost", "lowest_latency", "best_health"}:
            stage_result["strategy"] = strategy
        candidates = stage.get("candidates", [])
        for item in candidates[:100] if isinstance(candidates, list) else []:
            if not isinstance(item, dict) or not isinstance(item.get(id_field), str):
                continue
            obj = objects.get(item[id_field])
            if obj is None:
                continue
            label = (obj.display_name or obj.name) if id_field == "pool_id" else obj.deployment_id
            clean = {id_field: str(obj.pk), "pool" if id_field == "pool_id" else "source": label,
                     "selected": item[id_field] == stage_result[selected_key]}
            for key in ("rank", "priority", "weight", "source_count", "lowest_latency_ms", "latency_ms", "fallback_order"):
                value = item.get(key)
                if type(value) is int and 0 <= value <= 2**31 - 1:
                    clean[key] = value
            for key in ("best_health_status", "health_status"):
                if item.get(key) in ("healthy", "degraded", "unknown", "unhealthy", "unavailable"):
                    clean[key] = item[key]
            stage_result["candidates"].append(clean)
        result[stage_name] = stage_result
    return result if len(result) > 1 else None
