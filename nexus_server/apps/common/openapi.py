from __future__ import annotations

from typing import Any

from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.openapi import AutoSchema
from drf_spectacular.plumbing import build_serializer_context
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, inline_serializer
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from rest_framework.views import APIView


class NexusBearerAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "apps.common.authentication.NexusBearerAuthentication"
    name = "BearerAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT, sk-nexus API key, or sa-nexus service account token",
            "description": "Use `Authorization: Bearer <token>`.",
        }


class ErrorDetailSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()


class StandardErrorSerializer(serializers.Serializer):
    ok = serializers.BooleanField(default=False)
    data = serializers.JSONField(allow_null=True)
    error = ErrorDetailSerializer()
    request_id = serializers.CharField()


class GenericEnvelopeSerializer(serializers.Serializer):
    ok = serializers.BooleanField(default=True)
    data = serializers.JSONField()
    error = serializers.JSONField(allow_null=True, default=None)
    request_id = serializers.CharField()


class NexusAutoSchema(AutoSchema):
    def _get_serializer(self):
        view = self.view
        context = build_serializer_context(view)
        if isinstance(view, GenericAPIView):
            return super()._get_serializer()
        if isinstance(view, APIView):
            if callable(getattr(view, "get_serializer", None)):
                return view.get_serializer(context=context)
            if callable(getattr(view, "get_serializer_class", None)):
                return view.get_serializer_class()(context=context)
            if hasattr(view, "serializer_class"):
                serializer_class = view.serializer_class
                return serializer_class(context=context) if isinstance(serializer_class, type) else serializer_class
            return GenericEnvelopeSerializer(context=context)
        return super()._get_serializer()


def envelope_serializer(name: str, data_serializer: Any, *, many: bool = False):
    if isinstance(data_serializer, type):
        data_field = data_serializer(many=many, read_only=True)
    elif isinstance(data_serializer, serializers.BaseSerializer):
        data_serializer.read_only = True
        if many and not getattr(data_serializer, "many", False):
            data_field = serializers.ListField(child=serializers.DictField(), read_only=True)
        else:
            data_field = data_serializer
    else:
        data_field = serializers.JSONField(read_only=True)
    return inline_serializer(
        name=name,
        fields={
            "ok": serializers.BooleanField(default=True),
            "data": data_field,
            "error": serializers.JSONField(allow_null=True, default=None),
            "request_id": serializers.CharField(),
        },
    )


def success_response(name: str, data_serializer: Any, *, many: bool = False, status_code: int = 200) -> dict[int, OpenApiResponse]:
    return {
        status_code: OpenApiResponse(response=envelope_serializer(name, data_serializer, many=many)),
        400: OpenApiResponse(response=StandardErrorSerializer, description="Validation error."),
        401: OpenApiResponse(response=StandardErrorSerializer, description="Authentication failed."),
        403: OpenApiResponse(response=StandardErrorSerializer, description="Permission denied."),
        404: OpenApiResponse(response=StandardErrorSerializer, description="Resource not found."),
    }


TENANT_HEADER = OpenApiParameter(
    name="X-Nexus-Tenant",
    type=OpenApiTypes.UUID,
    location=OpenApiParameter.HEADER,
    required=True,
    description="Current Nexus tenant id.",
)
PROJECT_HEADER = OpenApiParameter(
    name="X-Nexus-Project",
    type=OpenApiTypes.UUID,
    location=OpenApiParameter.HEADER,
    required=False,
    description="Optional current Nexus project id.",
)
REQUEST_ID_HEADER = OpenApiParameter(
    name="X-Request-ID",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.HEADER,
    required=False,
    description="Optional caller-provided request id.",
)
TENANT_HEADERS = [TENANT_HEADER, PROJECT_HEADER, REQUEST_ID_HEADER]


def postprocess_schema(result: dict[str, Any], generator, request, public) -> dict[str, Any]:
    components = result.setdefault("components", {})
    security_schemes = components.setdefault("securitySchemes", {})
    security_schemes.setdefault(
        "TenantHeader",
        {
            "type": "apiKey",
            "in": "header",
            "name": "X-Nexus-Tenant",
            "description": "Current Nexus tenant id.",
        },
    )
    security_schemes.setdefault(
        "ProjectHeader",
        {
            "type": "apiKey",
            "in": "header",
            "name": "X-Nexus-Project",
            "description": "Optional current Nexus project id.",
        },
    )
    security_schemes.setdefault(
        "ApiKeyHeader",
        {
            "type": "apiKey",
            "in": "header",
            "name": "X-Api-Key",
            "description": "Alternative Nexus API key header for `sk-nexus-...` keys.",
        },
    )
    result.setdefault(
        "security",
        [
            {"BearerAuth": [], "TenantHeader": []},
            {"ApiKeyHeader": [], "TenantHeader": []},
        ],
    )
    result.setdefault("x-nexus-response-envelope", {"json_api_default": True})
    result.setdefault(
        "x-nexus-envelope-exceptions",
        [
            "OpenAI-compatible endpoints",
            "Claude native endpoints",
            "MCP/SSE/streaming endpoints",
            "file download endpoints",
            "Prometheus text/plain endpoint",
            "OpenAPI schema/docs endpoints",
        ],
    )
    return result
