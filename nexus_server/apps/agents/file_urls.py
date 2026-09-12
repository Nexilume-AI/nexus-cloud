"""Shared caller/delegate file routes; no Marketplace or billing composition."""
from django.urls import path
from .file_views import CallerFileView, CallerFileDownloadView, RunFilesView, RunFileDownloadView, InternalFileView, RunFileReferenceView
from .input_file_views import AgentComputerFilesView, AgentComputerFileImportView

urlpatterns = [
    path("agent-runs/<uuid:run_id>/file-reference/", RunFileReferenceView.as_view()),
    path("agent-files/", CallerFileView.as_view()),
    path("agent-files/<uuid:file_id>/", CallerFileView.as_view()),
    path("agent-files/<uuid:file_id>/download/", CallerFileDownloadView.as_view()),
    path("agents/<uuid:agent_id>/computer-files/", AgentComputerFilesView.as_view()),
    path("agents/<uuid:agent_id>/input-files/import/", AgentComputerFileImportView.as_view()),
    path("agent-runs/<uuid:run_id>/files/", RunFilesView.as_view()),
    path("agent-runs/<uuid:run_id>/files/<uuid:file_id>/download/", RunFileDownloadView.as_view()),
    path("internal/agent-runs/<uuid:run_id>/files/", InternalFileView.as_view()),
    path("internal/agent-runs/<uuid:run_id>/files/<uuid:file_id>/", InternalFileView.as_view()),
]
