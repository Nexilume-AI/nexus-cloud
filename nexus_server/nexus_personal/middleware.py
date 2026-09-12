"""Fixed personal request context; does not authorize resource operations."""
from django.core.exceptions import ImproperlyConfigured
from django.http import JsonResponse

from apps.common.context import reset_tenant_context, set_tenant_context
from .services import installation_context


class PersonalRequestIDMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        import re
        import uuid
        value = request.META.get("HTTP_X_REQUEST_ID") or "req_" + uuid.uuid4().hex
        if re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", value) is None:
            return JsonResponse({"code": "INVALID_REQUEST_ID", "message": "Use a request ID of at most 64 safe characters."}, status=400)
        request.request_id = value
        response = self.get_response(request)
        response["X-Request-ID"] = value
        if getattr(getattr(request, "resolver_match", None), "url_name", "") == "router-export-credentials":
            response["Cache-Control"] = "no-store"
            response["Pragma"] = "no-cache"
        return response


class PersonalContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            _, tenant_id, project_id = installation_context()
        except ImproperlyConfigured:
            return JsonResponse({"code": "PERSONAL_SETUP_REQUIRED",
                                 "message": "The operator must initialize or repair this personal instance."}, status=503)
        for header, fixed in (("HTTP_X_NEXUS_TENANT", tenant_id), ("HTTP_X_NEXUS_PROJECT", project_id)):
            if request.META.get(header, "") not in ("", fixed):
                return JsonResponse({"code": "PERSONAL_CONTEXT_MISMATCH",
                                     "message": "The requested context is unavailable."}, status=403)
        request.tenant_id, request.project_id = tenant_id, project_id
        token = set_tenant_context(tenant_id=tenant_id, project_id=project_id)
        try:
            return self.get_response(request)
        finally:
            reset_tenant_context(token)
