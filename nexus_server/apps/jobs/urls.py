from __future__ import annotations

from django.urls import path

from .views import JobDetailView, JobEventListView, JobListView


urlpatterns = [
    path("jobs/", JobListView.as_view(), name="job-list"),
    path("jobs/<uuid:job_id>/", JobDetailView.as_view(), name="job-detail"),
    path("jobs/<uuid:job_id>/events/", JobEventListView.as_view(), name="job-events"),
]
