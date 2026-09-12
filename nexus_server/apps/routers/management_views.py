"""Shared Router management HTTP handlers; invocation/trace compose separately."""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError
from apps.common import catalog_pagination
from .presentation import response_context
from .serializers import (RouterCreateSerializer, RouterChildBindingCreateSerializer,
    RouterChildBindingSerializer, RouterChildBindingUpdateSerializer, RouterDeploymentSerializer,
    RouterModelGroupBindSerializer, RouterModelGroupBindingSerializer, RouterPolicySerializer,
    RouterOutputSerializer, RouterOutputUpdateSerializer, RouterSerializer, RouterUpdateSerializer, RouterVersionSerializer)
from .services import (add_router_child_binding, bind_model_groups, create_router, deploy_router,
    delete_router, get_router, get_router_source, list_aggregation_candidates, list_router_child_bindings,
    list_router_outputs, list_routers, remove_router_child_binding, set_policy, update_router,
    update_router_child_binding, update_router_output, upload_router_file)


class RouterListCreateView(APIView):
    def get(self, request):
        routers = list_routers(request=request)
        query = (request.query_params.get("q") or "").strip()
        if len(query) > 200:
            raise ValidationError({"q": "Search is limited to 200 characters."})
        if query:
            routers = routers.filter(name__icontains=query)
        serialize = lambda rows: RouterSerializer(rows, many=True, context={"request": request}).data
        if catalog_pagination.requested(request):
            return Response(catalog_pagination.page(request=request, queryset=routers, serialize=serialize))
        return Response(serialize(routers))

    def post(self, request):
        serializer = RouterCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        router = create_router(request=request, **serializer.validated_data)
        return Response(RouterSerializer(router, context={"request": request}).data, status=status.HTTP_201_CREATED)


class RouterDetailView(APIView):
    def get(self, request, router_id):
        return Response(RouterSerializer(get_router(request=request, router_id=router_id), context={"request": request}).data)

    def patch(self, request, router_id):
        serializer = RouterUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        router = update_router(request=request, router_id=router_id, data=serializer.validated_data)
        return Response(RouterSerializer(router, context={"request": request}).data)

    def delete(self, request, router_id):
        delete_router(request=request, router_id=router_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class RouterUploadView(APIView):
    def post(self, request, router_id):
        version = upload_router_file(request=request, router_id=router_id, uploaded_file=request.FILES.get("file"))
        return Response(RouterVersionSerializer(version, **response_context(request)).data, status=status.HTTP_201_CREATED)


class RouterSourceView(APIView):
    def get(self, request, router_id):
        return Response(get_router_source(request=request, router_id=router_id))


class RouterDeployView(APIView):
    def post(self, request, router_id):
        deployment = deploy_router(request=request, router_id=router_id)
        return Response(RouterDeploymentSerializer(deployment, **response_context(request)).data, status=status.HTTP_201_CREATED)


class RouterModelGroupsView(APIView):
    def post(self, request, router_id):
        serializer = RouterModelGroupBindSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        bindings = bind_model_groups(
            request=request,
            router_id=router_id,
            providers=serializer.validated_data.get("providers"),
            model_group_ids=serializer.validated_data.get("model_group_ids"),
        )
        return Response(RouterModelGroupBindingSerializer(bindings, many=True, **response_context(request)).data)


class RouterOutputsView(APIView):
    def get(self, request, router_id):
        return Response(RouterOutputSerializer(list_router_outputs(request=request, router_id=router_id), many=True, **response_context(request)).data)


class RouterOutputDetailView(APIView):
    def patch(self, request, router_id, output_id):
        serializer = RouterOutputUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        output = update_router_output(
            request=request,
            router_id=str(router_id),
            output_id=str(output_id),
            data=serializer.validated_data,
        )
        return Response(RouterOutputSerializer(output, **response_context(request)).data)


class RouterChildBindingsView(APIView):
    def get(self, request, router_id):
        bindings = list_router_child_bindings(request=request, router_id=str(router_id))
        return Response(RouterChildBindingSerializer(bindings, many=True, **response_context(request)).data)

    def post(self, request, router_id):
        serializer = RouterChildBindingCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        binding = add_router_child_binding(
            request=request,
            router_id=str(router_id),
            data=serializer.validated_data,
        )
        return Response(RouterChildBindingSerializer(binding, **response_context(request)).data, status=status.HTTP_201_CREATED)


class RouterAggregationCandidatesView(APIView):
    def get(self, request, router_id):
        return Response(list_aggregation_candidates(request=request, router_id=str(router_id)))


class RouterChildBindingDetailView(APIView):
    def patch(self, request, router_id, binding_id):
        serializer = RouterChildBindingUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        binding = update_router_child_binding(
            request=request,
            router_id=str(router_id),
            binding_id=str(binding_id),
            data=serializer.validated_data,
        )
        return Response(RouterChildBindingSerializer(binding, **response_context(request)).data)

    def delete(self, request, router_id, binding_id):
        remove_router_child_binding(request=request, router_id=str(router_id), binding_id=str(binding_id))
        return Response(status=status.HTTP_204_NO_CONTENT)


class RouterPolicyView(APIView):
    def post(self, request, router_id):
        serializer = RouterPolicySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        router = set_policy(request=request, router_id=router_id, strategy=serializer.validated_data["strategy"])
        return Response(RouterSerializer(router, **response_context(request)).data)


