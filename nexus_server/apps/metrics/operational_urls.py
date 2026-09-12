"""Operational metrics and alerts only. No financial reports or privileged exporter."""
from django.urls import path
from .operational_views import (MetricsView, SystemMetricsView, MonitoringCapabilitiesView,
    AlertListCreateView, AlertDetailView, AlertEventListView, AlertEventDetailView, AlertTestView)

urlpatterns = [
    path("metrics/", MetricsView.as_view(), name="metrics"),
    path("metrics/system/", SystemMetricsView.as_view(), name="metrics-system"),
    path("metrics/capabilities/", MonitoringCapabilitiesView.as_view(), name="metrics-capabilities"),
    path("alerts/", AlertListCreateView.as_view(), name="alerts"),
    path("alerts/events/", AlertEventListView.as_view(), name="alert-events"),
    path("alerts/events/<str:event_id>/", AlertEventDetailView.as_view(), name="alert-event-detail"),
    path("alerts/<str:alert_id>/test/", AlertTestView.as_view(), name="alert-test"),
    path("alerts/<str:alert_id>/", AlertDetailView.as_view(), name="alert-detail"),
]
