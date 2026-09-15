"""Keep the OpenWrt device wire protocol independent of the console API."""
from rest_framework.renderers import JSONRenderer

from apps.common.renderers import UnifiedJSONRenderer


class PersonalJSONRenderer(JSONRenderer):
    def render(self, data, accepted_media_type=None, renderer_context=None):
        context = renderer_context or {}
        request = context.get("request")
        path = getattr(request, "path", "")
        if path == "/api/v1/edge/nodes/enroll/" or path.startswith("/api/v1/edge/v1/"):
            return UnifiedJSONRenderer().render(data, accepted_media_type, context)
        return super().render(data, accepted_media_type, context)
