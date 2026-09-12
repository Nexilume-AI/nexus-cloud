"""Personal discovery searches owned collections, never a public marketplace."""
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import APIView


class DatasetCapabilitiesView(APIView):
    def get(self, request):
        from apps.datasets.file_scanning import import_capabilities
        from apps.datasets.services import require_dataset_admin
        from apps.common.request_context import get_tenant_from_request
        require_dataset_admin(request=request, tenant=get_tenant_from_request(request))
        return Response({"can_create": True,
                         "can_search": True, "can_pull": True, "file_import": import_capabilities()})


class DatasetSearchView(APIView):
    def get(self, request):
        from apps.datasets.services import list_datasets, search_dataset_content
        from apps.datasets.serializers import DatasetSerializer, DatasetFileSerializer
        scope = request.query_params.get("scope", "metadata")
        if scope not in {"metadata", "content", "all"}:
            raise exceptions.ValidationError({"scope": "Use metadata, content or all."})
        keyword = str(request.query_params.get("q", ""))[:255]
        def content_results():
            return [{"dataset": DatasetSerializer(item["dataset"], context={"request": request}).data,
                     "file": DatasetFileSerializer(item["file"], context={"request": request}).data,
                     "matches": item["matches"]}
                    for item in search_dataset_content(request=request, keyword=keyword)]
        if scope == "content":
            return Response(content_results())
        rows = list_datasets(request=request)
        if keyword:
            rows = rows.filter(name__icontains=keyword)
        payload = DatasetSerializer(rows, many=True, context={"request": request}).data
        if scope == "all":
            return Response({"datasets": payload, "content": content_results() if keyword else []})
        return Response(payload)
