from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.deployments.models import Deployment
from apps.gateway.serializers import ChatCompletionRequestSerializer
from apps.gateway.services import chat_completions, resolve_router_routing
from apps.tenancy.services import get_tenant_from_request
from apps.common import catalog_pagination
from rest_framework.exceptions import ValidationError

from .serializers import (
    RouterCreateSerializer,
    RouterChildBindingCreateSerializer,
    RouterChildBindingSerializer,
    RouterChildBindingUpdateSerializer,
    RouterDeploymentSerializer,
    RouterModelGroupBindSerializer,
    RouterModelGroupBindingSerializer,
    RouterPolicySerializer,
    RouterOutputSerializer,
    RouterOutputUpdateSerializer,
    RouterPricingSerializer,
    RouterPricingSetSerializer,
    RouterProviderPreferenceCreateSerializer,
    RouterProviderPreferenceSerializer,
    RouterSerializer,
    RouterUpdateSerializer,
    RouterVersionSerializer,
)
from .services import (
    add_provider_preference,
    add_router_child_binding,
    bind_model_groups,
    create_router,
    deploy_router,
    delete_router,
    export_router_credentials,
    get_router,
    get_router_source,
    list_provider_preferences,
    list_aggregation_candidates,
    list_router_child_bindings,
    list_router_outputs,
    list_router_traces,
    list_routers,
    remove_provider_preference,
    remove_router_child_binding,
    set_policy,
    set_pricing,
    update_router,
    update_router_child_binding,
    update_router_output,
    upload_router_file,
)








































class RouterPricingView(APIView):
    def post(self, request, router_id):
        serializer = RouterPricingSetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        pricing = set_pricing(
            request=request,
            router_id=router_id,
            plan_id=serializer.validated_data["plan_id"],
            pricing_json=serializer.validated_data.get("pricing_json"),
        )
        return Response(RouterPricingSerializer(pricing).data)


class RouterProviderPreferenceView(APIView):
    def get(self, request, router_id):
        preferences = list_provider_preferences(request=request, router_id=router_id)
        return Response(RouterProviderPreferenceSerializer(preferences, many=True).data)

    def post(self, request, router_id):
        serializer = RouterProviderPreferenceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        preference = add_provider_preference(request=request, router_id=router_id, data=serializer.validated_data)
        return Response(RouterProviderPreferenceSerializer(preference).data, status=status.HTTP_201_CREATED)


class RouterProviderPreferenceDetailView(APIView):
    def delete(self, request, router_id, preference_id):
        preference = remove_provider_preference(request=request, router_id=router_id, preference_id=preference_id)
        return Response(RouterProviderPreferenceSerializer(preference).data)




class RouterTestView(APIView):
    def post(self, request, router_id):
        serializer = ChatCompletionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = dict(serializer.validated_data)
        payload["router_id"] = str(router_id)
        tenant = get_tenant_from_request(request)
        resolution = resolve_router_routing(request=request, tenant=tenant, router_id=str(router_id), payload=payload)
        selected = resolution.deployments[0]
        consumer_source_id = str(getattr(selected, "_nexus_consumer_source_id", selected.id))
        consumer_source = Deployment.objects.filter(tenant=tenant, id=consumer_source_id).first() or selected
        return Response(
            {
                "router_id": str(router_id),
                "strategy": get_router(request=request, router_id=router_id).strategy,
                "requested_model": payload["model"],
                "selected_model_group_id": getattr(selected, "_nexus_selected_model_group_id", ""),
                "selected_model_group": getattr(selected, "_nexus_selected_model_group_name", payload["model"]),
                "selected_source_id": str(consumer_source.id),
                "selected_source": consumer_source.deployment_id,
                "selected_provider": selected.provider.name,
                "selected_model": selected.model,
                "selected_provider_deployment_id": str(selected.id),
                "selected_provider_deployment": selected.deployment_id,
                "reason": "Selected by router cross-pool policy, then Model Pool source routing.",
                "trace": resolution.trace,
            }
        )


from .trace_views import RouterTraceListView
from .credential_views import RouterCredentialsExportView, RouterInvokeView

from .management_views import (RouterListCreateView, RouterDetailView, RouterUploadView, RouterSourceView, RouterDeployView, RouterModelGroupsView, RouterOutputsView, RouterOutputDetailView, RouterChildBindingsView, RouterAggregationCandidatesView, RouterChildBindingDetailView, RouterPolicyView)
