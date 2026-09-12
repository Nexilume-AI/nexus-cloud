"""Shared job/audit indexes; reporting and financial routes are not composed."""
from django.urls import path
from .operational_catalog import MonitoringJobsView, MonitoringJobEventsView, MonitoringAuditView
from .operational_actions import MonitoringRequestView, MonitoringActionView

urlpatterns = [
    path("metrics/requests/<str:request_id>/", MonitoringRequestView.as_view()),
    path("metrics/actions/<str:kind>/<str:record_id>/", MonitoringActionView.as_view()),
    path("metrics/jobs/", MonitoringJobsView.as_view()),
    path("metrics/jobs/<str:job_id>/events/", MonitoringJobEventsView.as_view()),
    path("metrics/jobs/<str:job_id>/", MonitoringJobsView.as_view()),
    path("metrics/audit/", MonitoringAuditView.as_view()),
    path("metrics/audit/<str:log_id>/", MonitoringAuditView.as_view()),
]
