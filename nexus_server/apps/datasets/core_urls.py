"""Personal file/data routes; Marketplace and acquisition routes are not installed."""
from django.urls import path
from .job_views import DatasetImportJobsView, DatasetImportJobView, DatasetRetentionView
from .manifest_views import DatasetVersionFilesView
from .views import (DatasetListCreateView, DatasetCapabilitiesView, DatasetSearchView, DatasetContentSearchView, DatasetDetailView, DatasetRenameView, DatasetVisibilityView, DatasetPullView, DatasetPushView, DatasetAgentTraceExportView, DatasetAgentMemoryExportView, DatasetAgentArtifactCaptureView, DatasetFileDownloadView, DatasetVersionListCreateView, DatasetVersionFileDownloadView, DatasetQuotaView, MediaAssetListCreateView, MediaAssetDetailView, MediaAssetSignedURLView, MediaAssetContentView)

urlpatterns = [
    path("datasets/<uuid:dataset_id>/versions/<uuid:version_id>/files/", DatasetVersionFilesView.as_view()),
    path("datasets/<uuid:dataset_id>/retention/", DatasetRetentionView.as_view()),
    path("datasets/<uuid:dataset_id>/imports/", DatasetImportJobsView.as_view()),
    path("datasets/<uuid:dataset_id>/imports/<uuid:job_id>/", DatasetImportJobView.as_view()),
    path("datasets/", DatasetListCreateView.as_view(), name="datasets"),
    path("datasets/capabilities/", DatasetCapabilitiesView.as_view(), name="dataset-capabilities"),
    path("datasets/search/", DatasetSearchView.as_view(), name="dataset-search"),
    path("datasets/content-search/", DatasetContentSearchView.as_view(), name="dataset-content-search"),
    path("datasets/<uuid:dataset_id>/", DatasetDetailView.as_view(), name="dataset-detail"),
    path("datasets/<uuid:dataset_id>/rename/", DatasetRenameView.as_view(), name="dataset-rename"),
    path("datasets/<uuid:dataset_id>/visibility/", DatasetVisibilityView.as_view(), name="dataset-visibility"),
    path("datasets/<uuid:dataset_id>/pull/", DatasetPullView.as_view(), name="dataset-pull"),
    path("datasets/<uuid:dataset_id>/push/", DatasetPushView.as_view(), name="dataset-push"),
    path("datasets/<uuid:dataset_id>/agent-assets/trace/", DatasetAgentTraceExportView.as_view(), name="dataset-agent-trace-export"),
    path("datasets/<uuid:dataset_id>/agent-assets/memory/", DatasetAgentMemoryExportView.as_view(), name="dataset-agent-memory-export"),
    path("datasets/<uuid:dataset_id>/agent-assets/artifact/", DatasetAgentArtifactCaptureView.as_view(), name="dataset-agent-artifact-capture"),
    path("datasets/<uuid:dataset_id>/files/<uuid:file_id>/download/", DatasetFileDownloadView.as_view(), name="dataset-file-download"),
    path("datasets/<uuid:dataset_id>/versions/", DatasetVersionListCreateView.as_view(), name="dataset-versions"),
    path("datasets/<uuid:dataset_id>/versions/<uuid:version_id>/files/<uuid:file_id>/download/", DatasetVersionFileDownloadView.as_view(), name="dataset-version-file-download"),
    path("datasets/<uuid:dataset_id>/quota/", DatasetQuotaView.as_view(), name="dataset-quota"),
    path("media/assets/", MediaAssetListCreateView.as_view(), name="media-assets"),
    path("media/assets/<uuid:asset_id>/", MediaAssetDetailView.as_view(), name="media-asset-detail"),
    path("media/assets/<uuid:asset_id>/signed-url/", MediaAssetSignedURLView.as_view(), name="media-asset-signed-url"),
    path("media/assets/<uuid:asset_id>/content/", MediaAssetContentView.as_view(), name="media-asset-content"),
]
