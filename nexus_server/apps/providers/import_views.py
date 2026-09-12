from django.http import HttpResponse
from django.db.models import Case, When, IntegerField
from django.utils import timezone
from rest_framework import exceptions, serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.authorization import has_nexus_permission
from apps.common.resource_catalog import resolve_ownership_project
from apps.common.request_context import get_tenant_from_request
from . import import_services as service
from .import_tables import API_COLUMNS, csv_bytes, read_text_table, read_upload, template_xlsx
from .models import ProviderImportBatch
from .services import can_create_provider


class PreviewInput(serializers.Serializer):
    request_key = serializers.UUIDField()
    duplicate_mode = serializers.ChoiceField(choices=["skip", "update"], default="skip")
    apis_text = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False, max_length=2_097_152)
    file = serializers.FileField(required=False)

    def validate(self, attrs):
        if set(self.initial_data) - set(self.fields):
            raise exceptions.ValidationError("Only API connection fields are accepted. Model Offers must be discovered, not imported.")
        return attrs


class CommitInput(serializers.Serializer):
    confirm_updates = serializers.BooleanField(default=False)
    allow_partial = serializers.BooleanField(default=False)
    retry_failed = serializers.BooleanField(default=False)


class ImportView(APIView):
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


class ProviderImportCapabilitiesView(ImportView):
    def get(self, request):
        tenant = get_tenant_from_request(request)
        project, _ = resolve_ownership_project(request=request, tenant=tenant, ownership=None)
        return Response({"can_import": can_create_provider(request=request, tenant=tenant, project=project),
                         "can_update_existing": has_nexus_permission(request.user, tenant, "admin"),
                         "max_apis": 100, "max_file_bytes": 2_097_152})


class ProviderImportListView(ImportView):
    def get(self, request):
        tenant, project, owner = service.import_context(request)
        rows = ProviderImportBatch.objects.filter(tenant=tenant, owner_subject_hash=owner,
            context_project_id=str(project.pk) if project else "").annotate(priority=Case(
                When(status__in=["preview", "processing", "partial"], expires_at__gt=timezone.now(), then=0),
                default=1, output_field=IntegerField())).only("id", "created_at", "status", "expires_at", "results").order_by("priority", "-created_at")[:20]
        return Response([{"id": str(row.pk), "created_at": row.created_at.isoformat(),
                          "status": "expired" if row.status in {"preview", "processing", "partial"} and row.expires_at <= timezone.now() else row.status,
                          "rows": len(row.results)} for row in rows])


class ProviderImportTemplateView(ImportView):
    def get(self, request):
        service.import_context(request)
        kind = request.query_params.get("file_format", "xlsx")
        if kind == "xlsx":
            content, filename, mime = template_xlsx(), "nexilume-provider-import.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        elif kind == "apis_csv":
            content, filename, mime = csv_bytes(API_COLUMNS), "apis.csv", "text/csv; charset=utf-8"
        else:
            raise exceptions.ValidationError("Choose xlsx or apis_csv.")
        response = HttpResponse(content, content_type=mime)
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class ProviderImportPreviewView(ImportView):
    def post(self, request):
        service.import_context(request)  # Authorize before parsing untrusted files.
        fields = PreviewInput(data=request.data)
        fields.is_valid(raise_exception=True)
        data = fields.validated_data
        if bool(data.get("file")) == bool(data.get("apis_text", "").strip()):
            raise exceptions.ValidationError("Upload an API file or paste an API table, not both.")
        apis = read_upload(data["file"]) if data.get("file") else read_text_table(data["apis_text"])
        batch = service.preview(request, apis, request_key=data["request_key"], duplicate_mode=data["duplicate_mode"])
        return Response(service.public_batch(batch), status=201)


class ProviderImportDetailView(ImportView):
    def get(self, request, batch_id):
        return Response(service.public_batch(service.get_batch(request, batch_id)))

    def delete(self, request, batch_id):
        batch = service.get_batch(request, batch_id)
        # Conditional update serializes with an in-flight chunk and never undoes completed APIs.
        ProviderImportBatch.objects.filter(pk=batch.pk).update(encrypted_payload="", status="discarded")
        batch.refresh_from_db()
        return Response(service.public_batch(batch))


class ProviderImportCommitView(ImportView):
    def post(self, request, batch_id):
        fields = CommitInput(data=request.data)
        fields.is_valid(raise_exception=True)
        return Response(service.public_batch(service.commit(request, batch_id, **fields.validated_data)))


class ProviderImportReportView(ImportView):
    def get(self, request, batch_id):
        batch = service.get_batch(request, batch_id)
        columns = ["sheet", "line", "api_ref", "name", "status", "message", "connection_id"]
        response = HttpResponse(csv_bytes(columns, [[row.get(key, "") for key in columns] for row in batch.results]), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="provider-import-results.csv"'
        return response
