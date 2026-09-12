from __future__ import annotations

from typing import Any

from rest_framework.renderers import JSONRenderer
from rest_framework.renderers import BaseRenderer
from django.utils.cache import patch_vary_headers

from .responses import success_response


class UnifiedJSONRenderer(JSONRenderer):
    media_type = "application/json"
    format = "json"

    def render(self, data: Any, accepted_media_type=None, renderer_context=None):
        renderer_context = renderer_context or {}
        request = renderer_context.get("request")
        response = renderer_context.get("response")
        request_id = getattr(request, "request_id", "") if request else ""

        if request and response and request.path.startswith("/api/") and not request.path.startswith("/api/v1/public/"):
            response["Cache-Control"] = "private, no-store"
            patch_vary_headers(response, ("Authorization", "Cookie", "X-Nexus-Tenant", "X-Nexus-Project"))

        if isinstance(data, dict) and {"ok", "data", "error", "request_id"}.issubset(data.keys()):
            if not data.get("request_id"):
                data["request_id"] = request_id
            return super().render(data, accepted_media_type, renderer_context)

        return super().render(
            success_response(data=data if data is not None else {}, request_id=request_id),
            accepted_media_type,
            renderer_context,
        )


class EventStreamRenderer(BaseRenderer):
    media_type = "text/event-stream"
    format = "event-stream"
    charset = None

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data
