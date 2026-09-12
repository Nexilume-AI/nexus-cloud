"""Owner-only historical runtime discovery and existing safe lifecycle cleanup."""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from apps.common import catalog_pagination
from apps.providers.runtime_services import list_provider_runtimes, get_provider_runtime, remove_provider_runtime


def summary(runtime):
    # Recovery needs identity/state only, never the runtime endpoint or secrets.
    return {"id": str(runtime.pk), "name": runtime.name, "runtime_type": runtime.runtime_type,
            "status": runtime.status, "source_provider_account_id": None,
            "created_at": runtime.created_at, "updated_at": runtime.updated_at}


class PersonalLegacyProviderListView(APIView):
    def get(self, request):
        rows = list_provider_runtimes(request=request).filter(source_provider_account__isnull=True)
        if catalog_pagination.requested(request):
            return Response(catalog_pagination.page(request=request, queryset=rows,
                serialize=lambda values: [summary(row) for row in values]))
        return Response([summary(row) for row in rows[:100]])


class PersonalLegacyProviderDetailView(APIView):
    def delete(self, request, runtime_id):
        runtime = get_provider_runtime(request=request, runtime_id=str(runtime_id))
        if runtime.source_provider_account_id is not None:
            raise ValidationError({"detail": "Remove the linked Provider through its deletion-impact workflow."})
        return Response(summary(remove_provider_runtime(request=request, runtime_id=str(runtime_id))))
