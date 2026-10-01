"""Shared operational Provider HTTP views with host-selected presentation."""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from .connection_inputs import (ProviderConnectionCreateSerializer, ProviderConnectionUpdateSerializer,
    ProviderConnectionRemoveSerializer, ProviderRuntimeModelOfferUpdateSerializer)
from .connection_presentation import connection_serializer
from .connection_services import (create_provider_connection, get_provider_connection, list_provider_connections,
    provider_connection_deletion_impact, remove_provider_connection, repair_provider_connection,
    require_connection_runtime, update_provider_connection)
from .runtime_services import (start_provider_runtime, stop_provider_runtime, check_provider_runtime_health,
    provider_runtime_login_info, refresh_provider_runtime_models, refresh_provider_runtime_model_offer,
    list_runtime_model_offers, update_provider_runtime_model_offer)

ProviderConnectionSerializer = connection_serializer()


class ProviderExecutionSetupView(APIView):
    def get(self, request):
        from apps.common.request_context import get_tenant_from_request
        from .execution_setup import execution_setup
        # The response contains only fixed capability codes, never host paths,
        # daemon versions, endpoints, image identifiers or credentials.
        get_tenant_from_request(request)
        return Response(execution_setup(), headers={"Cache-Control": "private, no-store"})


class ProviderConnectionListCreateView(APIView):
    def get(self, request):
        from apps.common import catalog_pagination
        summary = request.query_params.get("projection") == "summary"
        if catalog_pagination.requested(request):
            accounts = list_provider_connections(request=request, queryset_only=True, summary=summary)
            return Response(catalog_pagination.page(request=request, queryset=accounts,
                serialize=lambda rows: ProviderConnectionSerializer(rows, many=True, context={"request": request, "summary": summary}).data))
        accounts = list_provider_connections(request=request, summary=summary)
        return Response(ProviderConnectionSerializer(accounts, many=True, context={"request": request, "summary": summary}).data)

    def post(self, request):
        serializer = ProviderConnectionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        account = create_provider_connection(request=request, data=dict(serializer.validated_data))
        return Response(ProviderConnectionSerializer(account, context={"request": request}).data, status=status.HTTP_201_CREATED)


class ProviderConnectionDetailView(APIView):
    def get(self, request, account_id):
        return Response(ProviderConnectionSerializer(get_provider_connection(request=request, account_id=account_id), context={"request": request}).data)

    def patch(self, request, account_id):
        serializer = ProviderConnectionUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        account = update_provider_connection(
            request=request,
            account_id=account_id,
            data=dict(serializer.validated_data),
        )
        return Response(ProviderConnectionSerializer(account, context={"request": request}).data)


class ProviderConnectionDeletionImpactView(APIView):
    def get(self, request, account_id):
        return Response(provider_connection_deletion_impact(request=request, account_id=account_id))


class ProviderConnectionRemoveView(APIView):
    def post(self, request, account_id):
        serializer = ProviderConnectionRemoveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(remove_provider_connection(
            request=request,
            account_id=account_id,
            confirmation_name=serializer.validated_data["confirmation_name"],
        ))


class ProviderConnectionStartView(APIView):
    def post(self, request, account_id):
        _, runtime = require_connection_runtime(request=request, account_id=account_id)
        start_provider_runtime(request=request, runtime_id=str(runtime.id))
        return _connection_response(request, account_id)


class ProviderConnectionRepairView(APIView):
    def post(self, request, account_id):
        return Response(ProviderConnectionSerializer(
            repair_provider_connection(request=request, account_id=account_id), context={"request": request}
        ).data)


class ProviderConnectionStopView(APIView):
    def post(self, request, account_id):
        _, runtime = require_connection_runtime(request=request, account_id=account_id)
        stop_provider_runtime(request=request, runtime_id=str(runtime.id))
        return _connection_response(request, account_id)


class ProviderConnectionHealthView(APIView):
    def post(self, request, account_id):
        _, runtime = require_connection_runtime(request=request, account_id=account_id)
        check_provider_runtime_health(request=request, runtime_id=str(runtime.id))
        return _connection_response(request, account_id)


class ProviderConnectionLoginView(APIView):
    def get(self, request, account_id):
        _, runtime = require_connection_runtime(request=request, account_id=account_id)
        return Response(provider_runtime_login_info(request=request, runtime_id=str(runtime.id)))


class ProviderConnectionModelRefreshView(APIView):
    def post(self, request, account_id):
        if request.data:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"detail": "Model discovery accepts an empty request body."})
        _, runtime = require_connection_runtime(request=request, account_id=account_id)
        refresh_provider_runtime_models(request=request, runtime_id=str(runtime.id))
        return _connection_response(request, account_id)


class ProviderConnectionModelOfferRefreshView(APIView):
    def post(self, request, account_id, offer_id):
        if request.data:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"detail": "Model refresh accepts an empty request body."})
        _, runtime = require_connection_runtime(request=request, account_id=account_id)
        refresh_provider_runtime_model_offer(request=request, runtime_id=str(runtime.id), offer_id=str(offer_id))
        return _connection_response(request, account_id)


class ProviderConnectionModelDetailView(APIView):
    def patch(self, request, account_id, offer_id):
        _, runtime = require_connection_runtime(request=request, account_id=account_id)
        existing = list_runtime_model_offers(request=request, runtime_id=str(runtime.id)).filter(id=offer_id).first()
        if existing is None:
            return Response({"detail": "Model not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = ProviderRuntimeModelOfferUpdateSerializer(data=request.data, context={"offer": existing})
        serializer.is_valid(raise_exception=True)
        update_provider_runtime_model_offer(
            request=request,
            runtime_id=str(runtime.id),
            offer_id=str(offer_id),
            data=serializer.validated_data,
        )
        return _connection_response(request, account_id)


def _connection_response(request, account_id):
    return Response(ProviderConnectionSerializer(get_provider_connection(request=request, account_id=account_id), context={"request": request}).data)

