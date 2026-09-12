"""Operational bulk API import routes, composed before connection-ID routes."""
from django.urls import path
from .import_views import (
    ProviderImportCapabilitiesView, ProviderImportTemplateView, ProviderImportPreviewView,
    ProviderImportDetailView, ProviderImportCommitView, ProviderImportReportView, ProviderImportListView,
)

urlpatterns = [
    path("provider-connections/imports/", ProviderImportListView.as_view()),
    path("provider-connections/imports/capabilities/", ProviderImportCapabilitiesView.as_view()),
    path("provider-connections/imports/template/", ProviderImportTemplateView.as_view()),
    path("provider-connections/imports/preview/", ProviderImportPreviewView.as_view()),
    path("provider-connections/imports/<uuid:batch_id>/", ProviderImportDetailView.as_view()),
    path("provider-connections/imports/<uuid:batch_id>/commit/", ProviderImportCommitView.as_view()),
    path("provider-connections/imports/<uuid:batch_id>/report/", ProviderImportReportView.as_view()),
]
