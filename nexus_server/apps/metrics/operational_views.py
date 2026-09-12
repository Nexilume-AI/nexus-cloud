"""Shared operational HTTP surface; backend selection is explicit per distribution."""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from .serializers import AlertRuleSerializer, AlertRuleCreateSerializer, AlertEventSerializer
from .http_host import (get_resource_metrics, get_system_metrics, list_alerts, create_alert,
    delete_alert, list_alert_events, get_alert_event, test_alert)


class MetricsView(APIView):
    def get(self, request):
        resource_type = request.query_params.get("resource") or request.query_params.get("resource_type") or ""
        resource_id = request.query_params.get("id") or request.query_params.get("resource_id") or ""
        return Response(get_resource_metrics(request=request, resource_type=resource_type, resource_id=resource_id))


class SystemMetricsView(APIView):
    def get(self, request):
        return Response(get_system_metrics(request=request), headers={"Cache-Control": "private, no-store"})


class MonitoringCapabilitiesView(APIView):
    def get(self, request):
        from .policy import capabilities
        from .http_host import definitions
        allowed = capabilities(request)
        return Response({**allowed, "metrics": definitions(allowed["financial"])}, headers={"Cache-Control": "private, no-store"})
class AlertListCreateView(APIView):
    def get(self, request):
        from .catalog import respond
        return respond(request, list_alerts(request=request), AlertRuleSerializer, search_fields=("metric", "resource_id"))

    def post(self, request):
        serializer = AlertRuleCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        alert = create_alert(
            request=request,
            metric=serializer.validated_data["metric"],
            threshold=serializer.validated_data["threshold"],
            operator=serializer.validated_data["operator"],
            resource_type=serializer.validated_data["resource_type"],
            resource_id=serializer.validated_data["resource_id"],
            window_seconds=serializer.validated_data["window_seconds"],
            cooldown_seconds=serializer.validated_data["cooldown_seconds"],
            notification_channels=serializer.validated_data["notification_channels"],
        )
        return Response(AlertRuleSerializer(alert, context={"request": request}).data, status=status.HTTP_201_CREATED)


class AlertDetailView(APIView):
    def get(self, request, alert_id):
        from .operational_catalog import identifier
        from rest_framework.exceptions import NotFound
        row = list_alerts(request=request).filter(pk=identifier(alert_id)).first()
        if row is None:
            raise NotFound()
        return Response(AlertRuleSerializer(row, context={"request": request}).data)

    def delete(self, request, alert_id):
        return Response(AlertRuleSerializer(delete_alert(request=request, alert_id=alert_id), context={"request": request}).data)


class AlertEventListView(APIView):
    def get(self, request):
        from .catalog import respond
        return respond(request, list_alert_events(request=request), AlertEventSerializer, search_fields=("metric", "resource_id"))


class AlertEventDetailView(APIView):
    def get(self, request, event_id):
        return Response(AlertEventSerializer(get_alert_event(request=request, event_id=event_id), context={"request": request}).data)


class AlertTestView(APIView):
    def post(self, request, alert_id):
        return Response(AlertEventSerializer(test_alert(request=request, alert_id=alert_id), context={"request": request}).data, status=status.HTTP_201_CREATED)
