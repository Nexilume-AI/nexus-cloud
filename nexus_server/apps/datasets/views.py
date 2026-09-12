from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from .downloads import DatasetDownloadView
from .pagination import page, requested
from django.db.models import Count, Sum, Q

from apps.common.authorization import has_nexus_permission
from apps.common.request_context import get_tenant_from_request

from .serializers import (
    AgentArtifactCaptureSerializer,
    AgentMemoryExportSerializer,
    AgentTraceExportSerializer,
    DatasetCreateSerializer,
    DatasetFileSerializer,
    DatasetQuotaSerializer,
    DatasetQuotaSetSerializer,
    DatasetRenameSerializer,
    DatasetSerializer,
    DatasetVersionSerializer,
    DatasetVersionCreateSerializer,
    DatasetVisibilitySerializer,
    MediaAssetCreateSerializer,
    MediaAssetSerializer,
    MediaAssetSignedURLSerializer,
)
from .agent_asset_gateway import (
    capture_agent_artifact_to_dataset,
    export_agent_memory_to_dataset,
    export_agent_trace_to_dataset,
)
from .services import (
    create_dataset,
    create_version,
    dataset_acquisition_payload,
    delete_dataset,
    download_dataset_acquisition_file,
    download_dataset_file,
    download_marketplace_dataset_file,
    download_dataset_version_file,
    get_dataset,
    get_dataset_acquisition,
    get_marketplace_dataset,
    list_datasets,
    list_dataset_acquisitions,
    list_marketplace_datasets,
    pull_marketplace_dataset,
    list_versions,
    pull_dataset,
    push_dataset,
    rename_dataset,
    search_dataset_content,
    search_public_datasets,
    set_pricing,
    set_quota,
    set_visibility,
)
from .media_services import (
    delete_media_asset,
    get_media_asset,
    list_media_assets,
    open_signed_media_content,
    signed_media_url,
    upload_media_asset,
)


class DatasetListCreateView(APIView):
    def get(self, request):
        if requested(request):
            rows = list_datasets(request=request).prefetch_related(None)
            if request.query_params.get("q"):
                rows = rows.filter(name__icontains=request.query_params["q"][:255])
            summary = rows.aggregate(collections=Count("pk"), size_bytes=Sum("size_bytes"),
                published=Count("pk", filter=Q(visibility="public")), files=Sum("file_count"), drafts=Count("pk", filter=Q(file_count=0)))
            return Response(page(request, rows, lambda row: DatasetSerializer(row,
                context={"request": request, "compact": True}).data, summary=summary))
        return Response(DatasetSerializer(list_datasets(request=request), many=True, context={"request": request}).data)

    def post(self, request):
        serializer = DatasetCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        dataset = create_dataset(request=request, **serializer.validated_data)
        return Response(DatasetSerializer(dataset, context={"request": request}).data, status=status.HTTP_201_CREATED)


class DatasetContentSearchView(APIView):
    def get(self, request):
        return Response(serialize_content_results(search_dataset_content(request=request, keyword=request.query_params.get("q", "")), request=request))


class DatasetDetailView(APIView):
    def get(self, request, dataset_id):
        return Response(DatasetSerializer(get_dataset(request=request, dataset_id=dataset_id), context={"request": request}).data)

    def delete(self, request, dataset_id):
        return Response(DatasetSerializer(delete_dataset(request=request, dataset_id=dataset_id), context={"request": request}).data)


class DatasetRenameView(APIView):
    def post(self, request, dataset_id):
        serializer = DatasetRenameSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        dataset = rename_dataset(request=request, dataset_id=dataset_id, name=serializer.validated_data["name"])
        return Response(DatasetSerializer(dataset, context={"request": request}).data)


class DatasetVisibilityView(APIView):
    def post(self, request, dataset_id):
        serializer = DatasetVisibilitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        dataset = set_visibility(
            request=request,
            dataset_id=dataset_id,
            visibility=serializer.validated_data["visibility"],
        )
        return Response(DatasetSerializer(dataset, context={"request": request}).data)


class DatasetPullView(APIView):
    def get(self, request, dataset_id):
        if requested(request):
            dataset = get_dataset(request=request, dataset_id=dataset_id)
            rows = dataset.files.filter(status="active").select_related("dataset", "tenant", "project")
            summary = rows.aggregate(trace=Count("pk", filter=Q(metadata_json__source_type="agent_trace")),
                memory=Count("pk", filter=Q(metadata_json__source_type="agent_memory")),
                artifact=Count("pk", filter=Q(metadata_json__source_type="agent_artifact")),
                upload=Count("pk", filter=Q(metadata_json__source_type="user_upload")),
                agent_total=Count("metadata_json__agent_id", distinct=True))
            if request.query_params.get("q"):
                rows = rows.filter(file_name__icontains=request.query_params["q"][:255])
            result = page(request, rows, lambda row: DatasetFileSerializer(row).data, summary=summary)
            result["files"] = result.pop("items")
            result["dataset"] = DatasetSerializer(dataset, context={"request": request}).data
            return Response(result)
        result = pull_dataset(request=request, dataset_id=dataset_id)
        return Response(
            {
                "dataset": DatasetSerializer(result["dataset"], context={"request": request}).data,
                "files": DatasetFileSerializer(result["files"], many=True).data,
            }
        )


class DatasetPushView(APIView):
    def post(self, request, dataset_id):
        from .serializers import DatasetFileImportSerializer
        options = DatasetFileImportSerializer(data=request.data)
        options.is_valid(raise_exception=True)
        dataset_file = push_dataset(
            request=request,
            dataset_id=dataset_id,
            uploaded_file=options.validated_data["file"],
            rights_confirmed=options.validated_data["rights_confirmed"],
        )
        return Response(DatasetFileSerializer(dataset_file).data, status=status.HTTP_201_CREATED)


class DatasetVersionListCreateView(APIView):
    def get(self, request, dataset_id):
        versions = list_versions(request=request, dataset_id=dataset_id)
        if requested(request):
            return Response(page(request, versions, lambda row: DatasetVersionSerializer(row, context={"compact": True}).data))
        return Response(DatasetVersionSerializer(versions, many=True).data)

    def post(self, request, dataset_id):
        serializer = DatasetVersionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        version = create_version(
            request=request,
            dataset_id=dataset_id,
            release_notes=serializer.validated_data["release_notes"],
        )
        return Response(DatasetVersionSerializer(version, context={"compact": requested(request)}).data, status=status.HTTP_201_CREATED)


class DatasetAgentTraceExportView(APIView):
    def post(self, request, dataset_id):
        serializer = AgentTraceExportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        dataset_file = export_agent_trace_to_dataset(
            request=request,
            dataset_id=str(dataset_id),
            agent_id=str(serializer.validated_data["agent_id"]),
            run_id=str(serializer.validated_data["run_id"]),
        )
        return Response(DatasetFileSerializer(dataset_file).data, status=status.HTTP_201_CREATED)


class DatasetAgentMemoryExportView(APIView):
    def post(self, request, dataset_id):
        serializer = AgentMemoryExportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        dataset_file = export_agent_memory_to_dataset(
            request=request,
            dataset_id=str(dataset_id),
            agent_id=str(serializer.validated_data["agent_id"]),
            memory_item_ids=[str(value) for value in serializer.validated_data.get("memory_item_ids", [])],
        )
        return Response(DatasetFileSerializer(dataset_file).data, status=status.HTTP_201_CREATED)


class DatasetAgentArtifactCaptureView(APIView):
    def post(self, request, dataset_id):
        serializer = AgentArtifactCaptureSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        dataset_file = capture_agent_artifact_to_dataset(
            request=request,
            dataset_id=str(dataset_id),
            agent_id=str(serializer.validated_data["agent_id"]),
            artifact_id=str(serializer.validated_data["artifact_id"]),
        )
        return Response(DatasetFileSerializer(dataset_file).data, status=status.HTTP_201_CREATED)


class DatasetFileDownloadView(DatasetDownloadView):
    def get(self, request, dataset_id, file_id):
        return download_dataset_file(request=request, dataset_id=dataset_id, file_id=file_id)


class DatasetVersionFileDownloadView(DatasetDownloadView):
    def get(self, request, dataset_id, version_id, file_id):
        return download_dataset_version_file(
            request=request,
            dataset_id=dataset_id,
            version_id=version_id,
            file_id=file_id,
        )


class DatasetQuotaView(APIView):
    def post(self, request, dataset_id):
        serializer = DatasetQuotaSetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        quota = set_quota(request=request, dataset_id=dataset_id, max_size=serializer.validated_data["max_size"])
        return Response(DatasetQuotaSerializer(quota).data)


class MediaAssetListCreateView(APIView):
    def get(self, request):
        return Response(MediaAssetSerializer(list_media_assets(request=request), many=True).data)

    def post(self, request):
        serializer = MediaAssetCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        asset = upload_media_asset(
            request=request,
            uploaded_file=request.FILES.get("file"),
            purpose=serializer.validated_data["purpose"],
            expires_at=serializer.validated_data.get("expires_at"),
        )
        return Response(MediaAssetSerializer(asset).data, status=status.HTTP_201_CREATED)


class MediaAssetDetailView(APIView):
    def get(self, request, asset_id):
        return Response(MediaAssetSerializer(get_media_asset(request=request, asset_id=asset_id)).data)

    def delete(self, request, asset_id):
        return Response(MediaAssetSerializer(delete_media_asset(request=request, asset_id=asset_id)).data)


class MediaAssetSignedURLView(APIView):
    def post(self, request, asset_id):
        serializer = MediaAssetSignedURLSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            {
                "url": signed_media_url(
                    request=request,
                    asset_id=asset_id,
                    expires_in=serializer.validated_data.get("expires_in"),
                )
            }
        )


class MediaAssetContentView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, asset_id):
        return open_signed_media_content(
            asset_id=str(asset_id),
            token=str(request.query_params.get("token") or ""),
        )


def serialize_content_results(results, request=None):
    return [
        {
            "dataset": DatasetSerializer(item["dataset"], **({"context": {"request": request}} if request is not None else {})).data,
            "file": DatasetFileSerializer(item["file"]).data,
            "matches": item["matches"],
        }
        for item in results
    ]


from .presentation import view_export


def __getattr__(name):
    if name in ["DatasetCapabilitiesView","DatasetSearchView","DatasetPricingView","MarketplaceDatasetListView","MarketplaceDatasetDetailView","MarketplaceDatasetPullView","MarketplaceDatasetAcquisitionCreateView","DatasetAcquisitionListView","DatasetAcquisitionDetailView","DatasetAcquisitionFileDownloadView","MarketplaceDatasetFileDownloadView"]:
        return view_export(name)
    raise AttributeError(name)
