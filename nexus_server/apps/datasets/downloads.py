"""Native, authenticated downloads with bounded-memory WSGI/ASGI streaming."""
from __future__ import annotations

import hashlib
import time
import uuid

from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.handlers.asgi import ASGIRequest
from django.http import HttpResponse, StreamingHttpResponse
from django.utils.http import content_disposition_header, parse_etags
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.subjects import request_subject
from apps.common.request_context import get_tenant_from_request
from .storage_backends import get_dataset_storage_backend
from .transfers import reserve_export, complete_export, renew

CHUNK_SIZE = 256 * 1024
COOKIE_AGE = 3600
SALT = "nexus.dataset.download.v1"


def cookie_name(path):
    return "nexus_dl_" + hashlib.sha256(path.encode()).hexdigest()[:20]


class DownloadCookieAuthentication(BaseAuthentication):
    def authenticate_header(self, request):
        # Keep the ordinary API's 401 challenge/refresh behavior when bearer
        # credentials expire; the cookie authenticator must not turn it into 403.
        return "Bearer"

    def authenticate(self, request):
        if request.method not in {"GET", "HEAD"} or request.headers.get("Authorization") or request.headers.get("X-Api-Key"):
            return None
        token = request.COOKIES.get(cookie_name(request.path))
        if not token:
            return None
        try:
            data = signing.loads(token, salt=SALT, max_age=COOKIE_AGE)
            if data["path"] != request.path:
                raise ValueError()
            user = get_user_model().objects.get(pk=data["user"], is_active=True)
            if data["auth_hash"] != user.get_session_auth_hash():
                raise ValueError()
            session_user = getattr(request._request, "user", None)
            if getattr(session_user, "is_authenticated", False) and session_user.pk != user.pk:
                raise ValueError()
            request.tenant_id, request.project_id = data["tenant"], data["project"]
            request.dataset_download_id = data["download_id"]
            return user, None
        except (signing.BadSignature, KeyError, ValueError, get_user_model().DoesNotExist):
            raise exceptions.NotFound("Download session unavailable. Start the download again.") from None


class DatasetDownloadView(APIView):
    authentication_classes = [DownloadCookieAuthentication, *APIView.authentication_classes]

    def post(self, request, *args, **kwargs):
        # All four routes perform their normal authorization before preparing the cookie.
        return self.get(request, *args, **kwargs)


def prepare_download(request, file_name, size):
    subject = request_subject(request)
    if subject.principal_type != "user":
        raise exceptions.ValidationError("Machine identities must use authenticated GET with X-Nexus-Download-Id for resume.")
    tenant = get_tenant_from_request(request)
    download_id = str(uuid.uuid4())
    token = signing.dumps({
        "path": request.path, "user": str(request.user.pk), "auth_hash": request.user.get_session_auth_hash(),
        "tenant": str(tenant.pk), "project": str(getattr(request, "project_id", "") or ""),
        "download_id": download_id,
    }, salt=SALT, compress=True)
    response = Response({"url": request.path, "file_name": file_name, "size_bytes": size, "expires_in": COOKIE_AGE})
    response.set_cookie(cookie_name(request.path), token, max_age=COOKIE_AGE, path=request.path,
        secure=request.is_secure(), httponly=True, samesite="Strict")
    response["Cache-Control"] = "no-store"
    return response


def selected_range(value, size):
    """Single byte range; malformed/multiple/unsatisfiable ranges fail explicitly."""
    try:
        if not value.startswith("bytes=") or "," in value:
            raise ValueError()
        left, right = value[6:].split("-", 1)
        if not size or (not left and not right):
            raise ValueError()
        if not left:
            suffix = int(right)
            if suffix <= 0:
                raise ValueError()
            start, end = max(0, size - suffix), size - 1
        else:
            start = int(left)
            end = min(int(right), size - 1) if right else size - 1
        if start < 0 or start >= size or end < start:
            raise ValueError()
        return start, end - start + 1
    except (ValueError, TypeError):
        return None


class DownloadStream:
    def __init__(self, stream, length, transfer):
        self.stream, self.remaining, self.transfer = stream, length, transfer
        self.last_renewal = time.monotonic()

    def read(self, *, renew_lease=True):
        if renew_lease and time.monotonic() - self.last_renewal >= 30:
            renew(self.transfer)
            self.last_renewal = time.monotonic()
        if self.remaining == 0:
            return b""
        chunk = self.stream.read(min(CHUNK_SIZE, self.remaining))
        if not chunk:
            raise OSError("Dataset stream ended before its declared size.")
        self.remaining -= len(chunk)
        return chunk

    def close(self):
        self.stream.close()

    def __iter__(self):
        try:
            while self.remaining:
                chunk = self.read()
                # Commit before sending the last byte. Retries/ranges share this one
                # logical file export; bytes-on-wire are not purchase billing.
                if not self.remaining:
                    complete_export(self.transfer)
                yield chunk
            if not self.transfer.size_bytes:
                complete_export(self.transfer)
        finally:
            self.close()

    async def aiter(self):
        try:
            while self.remaining:
                if time.monotonic() - self.last_renewal >= 30:
                    # Keep ORM work on the request's database thread, not in the
                    # shared storage-I/O pool (which has no request DB cleanup).
                    await sync_to_async(renew, thread_sensitive=True)(self.transfer)
                    self.last_renewal = time.monotonic()
                chunk = await sync_to_async(self.read, thread_sensitive=False)(renew_lease=False)
                if not self.remaining:
                    await sync_to_async(complete_export, thread_sensitive=True)(self.transfer)
                yield chunk
            if not self.transfer.size_bytes:
                await sync_to_async(complete_export, thread_sensitive=True)(self.transfer)
        finally:
            await sync_to_async(self.close, thread_sensitive=False)()


def file_response(*, request, dataset, object_key, storage_backend, file_name, size, sha256, content_type, action, file_id):
    from .services import log_write
    if request.method == "GET" and getattr(request, "query_params", getattr(request, "GET", {})).get("preview") == "1":
        from .previews import image_preview
        return image_preview(request=request, dataset=dataset, object_key=object_key, storage_backend=storage_backend,
            size=size, sha256=sha256, content_type=content_type, file_id=file_id)
    if request.method == "POST":
        return prepare_download(request, file_name, size)
    fingerprint = hashlib.sha256(f"{storage_backend}|{object_key}|{size}|{sha256}".encode()).hexdigest()
    etag = f'"{fingerprint}"'
    headers = {"ETag": etag, "Accept-Ranges": "bytes", "Cache-Control": "private, no-store",
        "Content-Disposition": content_disposition_header(True, file_name), "X-Content-Type-Options": "nosniff"}
    validators = parse_etags(request.headers.get("If-None-Match", ""))
    if any(value.removeprefix("W/") == etag or value == "*" for value in validators):
        return HttpResponse(status=304, headers=headers)
    start, length, status = 0, size, 200
    range_header = request.headers.get("Range") if request.method != "HEAD" else None
    if range_header and request.headers.get("If-Range", etag) == etag:
        selection = selected_range(range_header, size)
        if selection is None:
            return HttpResponse(status=416, headers={**headers, "Content-Range": f"bytes */{size}"})
        start, length = selection
        status = 206
        headers["Content-Range"] = f"bytes {start}-{start + length - 1}/{size}"
    headers["Content-Length"] = str(length)
    if request.method == "HEAD":
        return HttpResponse(headers=headers, content_type=content_type)
    raw_id = getattr(request, "dataset_download_id", "") or request.headers.get("X-Nexus-Download-Id") or str(uuid.uuid4())
    try:
        download_id = str(uuid.UUID(raw_id))
    except ValueError:
        raise exceptions.ValidationError({"download_id": "X-Nexus-Download-Id must be a UUID."}) from None
    identity = hashlib.sha256(f"{request_subject(request).subject_hash}|{download_id}|{fingerprint}".encode()).hexdigest()
    backend = get_dataset_storage_backend(storage_backend)
    stream = backend.open_range(object_key=object_key, start=start, length=length) if status == 206 else backend.open(object_key=object_key)
    try:
        transfer = reserve_export(dataset, size, identity)
        log_write(request=request, action=action, dataset=dataset, metadata={"file_id": str(file_id), "file_name": file_name})
        body = DownloadStream(stream, length, transfer)
        raw_request = getattr(request, "_request", request)
        response = StreamingHttpResponse(body.aiter() if isinstance(raw_request, ASGIRequest) else body,
            status=status, headers=headers, content_type=content_type)
        # Also close unopened iterators (e.g. HEAD conversion or client cancellation).
        if response.is_async:
            response._resource_closers.append(body.close)
        response["X-Nexus-Download-Id"] = download_id
        return response
    except BaseException:
        stream.close()
        raise
