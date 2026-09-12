from __future__ import annotations

import uuid
from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse

from .context import reset_tenant_context, set_tenant_context


class RequestIDMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id = request.META.get(settings.NEXUS_REQUEST_ID_HEADER) or f"req_{uuid.uuid4().hex}"
        request.request_id = request_id  # type: ignore[attr-defined]
        response = self.get_response(request)
        response["X-Request-ID"] = request_id
        return response


class TenantContextMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        tenant_id = request.META.get(settings.NEXUS_TENANT_HEADER, "")
        project_id = request.META.get(settings.NEXUS_PROJECT_HEADER, "")
        request.tenant_id = tenant_id  # type: ignore[attr-defined]
        request.project_id = project_id  # type: ignore[attr-defined]
        token = set_tenant_context(tenant_id=tenant_id, project_id=project_id)
        try:
            return self.get_response(request)
        finally:
            reset_tenant_context(token)
