from __future__ import annotations

from django.urls import path

from .views import AuditLogDetailView, AuditLogListView


urlpatterns = [
    path("audit/logs/", AuditLogListView.as_view(), name="audit-logs"),
    path("audit/logs/<uuid:log_id>/", AuditLogDetailView.as_view(), name="audit-log-detail"),
]
