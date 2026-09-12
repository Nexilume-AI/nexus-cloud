"""Private large-file HTTP data plane, separate from MCP JSON messages."""
from asgiref.sync import sync_to_async
from django.core.handlers.asgi import ASGIRequest
from django.http import HttpResponse, StreamingHttpResponse
from django.utils.http import content_disposition_header
from rest_framework import exceptions, serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.datasets.downloads import DownloadCookieAuthentication, prepare_download, selected_range
from apps.datasets.storage_backends import get_dataset_storage_backend
from . import file_transfers as transfers
from .models import AgentFileTransfer
from .services import get_private_display_run


class CreateFile(serializers.Serializer):
    agent_id = serializers.UUIDField(required=False)
    name = serializers.CharField(max_length=255)
    size_bytes = serializers.IntegerField(min_value=0)
    content_type = serializers.RegexField(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$", max_length=255, required=False)
    sha256 = serializers.RegexField(r"^[a-f0-9]{64}$", required=False)
    source_kind = serializers.ChoiceField(choices=("upload", "audio"), default="upload")
    source_label = serializers.CharField(max_length=512, required=False, allow_blank=True)
    idempotency_key = serializers.CharField(max_length=128, required=False, allow_blank=True)


class FileStream:
    def __init__(self, stream, length):
        self.stream, self.remaining = stream, length

    def read(self):
        data = self.stream.read(min(transfers.STREAM_CHUNK, self.remaining))
        if not data and self.remaining:
            raise OSError("File stream truncated")
        self.remaining -= len(data)
        return data

    def close(self):
        self.stream.close()

    def __iter__(self):
        try:
            while self.remaining:
                yield self.read()
        finally:
            self.close()

    async def aiter(self):
        try:
            while self.remaining:
                yield await sync_to_async(self.read, thread_sensitive=False)()
        finally:
            await sync_to_async(self.close, thread_sensitive=False)()


def stream_file(request, *, backend, key, name, size, sha256):
    if request.method == "POST":
        return prepare_download(request, name, size)
    etag = '"' + sha256 + '"'
    headers = {"Accept-Ranges": "bytes", "ETag": etag, "X-Nexus-File-SHA256": sha256,
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox", "Content-Disposition": content_disposition_header(True, name)}
    start, length, status = 0, size, 200
    value = request.headers.get("Range")
    if value and request.method != "HEAD" and request.headers.get("If-Range", etag) == etag:
        selection = selected_range(value, size)
        if selection is None:
            return HttpResponse(status=416, headers={**headers, "Content-Range": f"bytes */{size}"})
        start, length = selection
        status = 206
        headers["Content-Range"] = f"bytes {start}-{start+length-1}/{size}"
    headers["Content-Length"] = str(length)
    if request.method == "HEAD":
        return HttpResponse(headers=headers, content_type="application/octet-stream")
    store = get_dataset_storage_backend(backend)
    stream = store.open_range(object_key=key, start=start, length=length) if status == 206 else store.open(object_key=key)
    body = FileStream(stream, length)
    response = StreamingHttpResponse(body.aiter() if isinstance(request._request, ASGIRequest) else body,
        status=status, headers=headers, content_type="application/octet-stream")
    response._resource_closers.append(body.close)
    return response


def transfer_download(request, row):
    from .file_directory import validate_reference
    validate_reference(row)
    if row.state != "ready" or not row.object_key:
        raise exceptions.NotFound("File is not ready.")
    return stream_file(request, backend=row.storage_backend, key=row.object_key, name=row.name, size=row.size_bytes, sha256=row.sha256)


class CallerFileView(APIView):
    def get(self, request, file_id=None):
        return Response(transfers.metadata(transfers.owner_transfer(request, file_id)) if file_id else transfers.limits())

    def post(self, request, file_id=None):
        if file_id:
            return Response(transfers.metadata(transfers.complete_transfer(transfers.owner_transfer(request, file_id))), status=202)
        data = CreateFile(data=request.data)
        data.is_valid(raise_exception=True)
        if not data.validated_data.get("agent_id"):
            raise exceptions.ValidationError({"agent_id": "Required."})
        return Response(transfers.metadata(transfers.create_transfer(request=request, data=data.validated_data)), status=201)

    def put(self, request, file_id):
        row = transfers.owner_transfer(request, file_id)
        return receive_part(request, row)

    def delete(self, request, file_id):
        transfers.cancel_transfer(transfers.owner_transfer(request, file_id))
        return Response(status=204)


def receive_part(request, row):
    try:
        offset = int(request.headers.get("X-Nexus-Upload-Offset", "-1"))
        declared_size = int(request.META.get("CONTENT_LENGTH", 0))
    except (ValueError, TypeError):
        raise exceptions.ValidationError("Invalid upload headers.") from None
    if not 0 < declared_size <= transfers.CHUNK_SIZE:
        raise exceptions.ValidationError("Each upload request must contain at most one MiB.")
    return Response(transfers.metadata(transfers.put_part(row, offset=offset, body=request.body,
        digest=request.headers.get("X-Nexus-Chunk-SHA256", ""))))


class CallerFileDownloadView(APIView):
    authentication_classes = [DownloadCookieAuthentication, *APIView.authentication_classes]

    def get(self, request, file_id):
        row = transfers.owner_transfer(request, file_id)
        if row.run_id:
            raise exceptions.NotFound("Use this Run's private download endpoint.")
        return transfer_download(request, row)

    post = get
    head = get


class RunFilesView(APIView):
    def get(self, request, run_id):
        run = get_private_display_run(request=request, run_id=str(run_id))
        if request.query_params.get("paged") == "1":
            from .file_directory import directory_page
            return Response(directory_page(request, run,
                run.file_transfers.filter(direction="input", state="ready"), "input", transfers.metadata))
        return Response([transfers.metadata(row) for row in run.file_transfers.filter(direction="input", state="ready").order_by("created_at")[:200]])


class RunFileReferenceView(APIView):
    """Select immutable Run-owned content, never a caller-supplied storage URL."""
    def get(self, request, run_id):
        return self.resolve(request, run_id, request.query_params, create=False)

    def post(self, request, run_id):
        return self.resolve(request, run_id, request.data, create=True)

    def resolve(self, request, run_id, data, create):
        from django.db import transaction
        from datetime import timedelta
        from django.utils import timezone
        from .models import AgentDisplayRun
        from .file_directory import reference_output, validate_reference
        class Selection(serializers.Serializer):
            kind = serializers.ChoiceField(choices=("input", "output", "image"))
            id = serializers.UUIDField()
        selection = Selection(data=data)
        selection.is_valid(raise_exception=True)
        run = get_private_display_run(request=request, run_id=str(run_id))
        kind, pk = selection.validated_data["kind"], selection.validated_data["id"]
        if kind == "image":
            asset = run.display_assets.filter(pk=pk).first()
            if not asset:
                raise exceptions.NotFound("Image not found.")
            return Response({"kind": "run_image", "id": str(asset.pk), "name": "Run image",
                "size": asset.size_bytes, "contentType": asset.content_type, "runId": str(run.pk)})
        if kind == "output":
            if not create:
                raise exceptions.ValidationError("Select an output before using its reference.")
            # A reference shares the same immutable, Run-owned object. It has no
            # upload parts, performs no scan/write/copy, and cannot be cancelled
            # independently of its Run. Repeated selections reuse one reference.
            with transaction.atomic():
                AgentDisplayRun.objects.select_for_update().get(pk=run.pk)
                artifact = reference_output(run, pk)
                row, _ = AgentFileTransfer.objects.get_or_create(run=run, idempotency_key=f"reference:{pk}", defaults={
                    "tenant_id": run.consumer_tenant_id, "project_id": run.consumer_project_id,
                    "agent_id": run.agent_id, "caller_subject_hash": run.caller_subject_hash,
                    "direction": "input", "name": artifact.original_file_name, "content_type": artifact.content_type,
                    "size_bytes": artifact.size_bytes, "received_bytes": artifact.size_bytes,
                    "sha256": artifact.sha256, "state": "ready", "object_key": artifact.snapshot_object_key,
                    "storage_backend": artifact.snapshot_storage_backend, "source_kind": "run_output",
                    "source_label": str(pk), "expires_at": timezone.now() + timedelta(days=1)})
        else:
            row = transfers.owner_transfer(request, pk, run=run)
        if row.direction != "input" or row.state != "ready" or row.source_kind == "audio":
            raise exceptions.NotFound("File not ready for reuse.")
        validate_reference(row)
        return Response({"kind": "run_file", "id": str(row.pk), "name": row.name,
            "size": row.size_bytes, "contentType": row.content_type, "runId": str(run.pk)})


class RunFileDownloadView(APIView):
    authentication_classes = [DownloadCookieAuthentication, *APIView.authentication_classes]

    def get(self, request, run_id, file_id):
        run = get_private_display_run(request=request, run_id=str(run_id), require_token=not bool(getattr(request,"dataset_download_id",None)))
        return transfer_download(request, transfers.owner_transfer(request, file_id, run=run))

    post = get
    head = get


class InternalFileView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def run(self, request, run_id):
        from .runtime_services import get_internal_interaction_run
        run = get_internal_interaction_run(run_id=str(run_id), token=request.headers.get("X-Nexus-Interaction-Token", ""))
        if run.run_kind != "invocation" or run.status != "running" or run.caller_hidden_at:
            raise exceptions.NotFound("Active Run not found.")
        return run

    def get(self, request, run_id, file_id=None):
        run = self.run(request, run_id)
        if not file_id:
            return Response([transfers.metadata(row) for row in run.file_transfers.filter(direction="input", state="ready")[:200]])
        row = transfers.owner_transfer(request, file_id, run=run)
        if request.query_params.get("download") == "1":
            return transfer_download(request, row)
        return Response(transfers.metadata(row))

    head = get

    def post(self, request, run_id, file_id=None):
        run = self.run(request, run_id)
        if file_id:
            row = transfers.owner_transfer(request, file_id, run=run)
            if row.direction != "output":
                raise exceptions.NotFound("Output upload not found.")
            return Response(transfers.metadata(transfers.complete_transfer(row)), status=202)
        data = CreateFile(data=request.data)
        data.is_valid(raise_exception=True)
        return Response(transfers.metadata(transfers.create_transfer(request=request, data=data.validated_data, run=run)), status=201)

    def put(self, request, run_id, file_id):
        row = transfers.owner_transfer(request, file_id, run=self.run(request, run_id))
        if row.direction != "output":
            raise exceptions.NotFound("Output upload not found.")
        return receive_part(request, row)

    def delete(self, request, run_id, file_id):
        row = transfers.owner_transfer(request, file_id, run=self.run(request, run_id))
        if row.direction != "output":
            raise exceptions.NotFound("Output upload not found.")
        transfers.cancel_transfer(row)
        return Response(status=204)
