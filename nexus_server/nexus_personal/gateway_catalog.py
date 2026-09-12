"""Currently callable owner models, without a commercial API Key policy."""
from rest_framework import exceptions
from rest_framework.response import Response
from apps.common.request_context import get_tenant_from_request
from apps.deployments.models import ModelGroup
from apps.deployments.candidates import model_group_deployment_candidates
from apps.gateway.capabilities import deployment_contract
from apps.routers.models import Router
from .resource_catalog import PersonalResourceCatalog


def model_rows(request):
    from apps.gateway.services import router_model_catalog, is_uuid
    from .gateway_integration import authorize_router
    from .gateway_lifecycle import context
    tenant = get_tenant_from_request(request)
    context(request, tenant)
    catalog = PersonalResourceCatalog()
    router_id = request.query_params.get("router_id")
    from .router_credentials import current_credential
    credential = current_credential(request)
    if credential is not None:
        if router_id and str(router_id) != str(credential.router_id):
            raise exceptions.PermissionDenied("This credential cannot inspect another Router.")
        router_id = str(credential.router_id)
    if router_id is not None:
        if not is_uuid(router_id):
            raise exceptions.NotFound("Router is unavailable.")
        router = catalog.discoverable_resource_queryset(Router.objects.all(), request=request,
            tenant=tenant, resource_type="router").filter(pk=router_id, status=Router.STATUS_DEPLOYED).first()
        if router is None:
            raise exceptions.NotFound("Router is unavailable.")
        authorize_router(request=request, tenant=tenant, router=router)
        return [{"id": row["id"], "model_contract": row.get("model_contract", {})}
                for row in router_model_catalog(tenant=tenant, router=router) if row["available"]
                and (credential is None or row["id"] in credential.model_names)]
    groups = catalog.discoverable_resource_queryset(ModelGroup.objects.all(), request=request,
        tenant=tenant, resource_type="model_group").filter(status="active").order_by("name", "created_at")
    rows = []
    seen = set()
    for group in groups:
        if group.name in seen:
            continue
        # Match direct Gateway resolution, which chooses the first owned Pool.
        seen.add(group.name)
        sources = [source for source in model_group_deployment_candidates(tenant=tenant, group=group)
                   if source.health_status != "unhealthy"]
        if not sources:
            continue
        contracts = [deployment_contract(source) for source in sources]
        rows.append({"id": group.name, "model_contract": {
            field: sorted({value for contract in contracts for value in contract[field]})
            for field in ("operations", "input_modalities", "output_modalities")}})
    return rows


def openai_model_catalog(*, view, request):
    return Response({"object": "list", "data": [
        {"id": row["id"], "object": "model", "created": 0, "owned_by": "nexus",
         "model_contract": row["model_contract"]} for row in model_rows(request)]})


def claude_model_catalog(*, view, request):
    return Response({"data": [{"type": "model", "id": row["id"], "display_name": row["id"],
                              "created_at": None} for row in model_rows(request)], "has_more": False})


def response_events(*, events):
    # Responses already emitted response.created before admission can fail.
    # Translate a typed refusal into response.failed, not a broken HTTP stream
    # or a false response.completed. Never dispatch or retry another request.
    from apps.gateway.provider_clients import ProviderStreamEvent
    try:
        yield from events
    except exceptions.APIException as exc:
        yield ProviderStreamEvent(error_code=str(getattr(exc, "default_code", "GATEWAY_ERROR")).upper(),
                                  error_message=str(exc.detail))
    finally:
        events.close()
