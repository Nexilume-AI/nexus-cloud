from __future__ import annotations

import logging
from typing import Any

from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler

from .responses import error_response

logger = logging.getLogger(__name__)


class NexusAPIException(exceptions.APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Bad request."
    default_code = "BAD_REQUEST"


def _error_code(exc: Exception) -> str:
    if isinstance(exc, exceptions.NotAuthenticated):
        return "NOT_AUTHENTICATED"
    if isinstance(exc, exceptions.AuthenticationFailed):
        return "AUTHENTICATION_FAILED"
    if isinstance(exc, exceptions.PermissionDenied):
        return "PERMISSION_DENIED"
    if isinstance(exc, Http404):
        return "NOT_FOUND"
    if isinstance(exc, exceptions.ValidationError):
        return "VALIDATION_ERROR"
    code = getattr(exc, "default_code", None) or getattr(exc, "code", None)
    return str(code or exc.__class__.__name__).upper()


def _message(detail: Any) -> str:
    if isinstance(detail, list):
        return "; ".join(_message(item) for item in detail)
    if isinstance(detail, dict):
        parts = []
        for field, value in detail.items():
            parts.append(f"{field}: {_message(value)}")
        return "; ".join(parts)
    return str(detail)


def unified_exception_handler(exc: Exception, context: dict[str, Any]):
    response = exception_handler(exc, context)
    request = context.get("request")
    request_id = getattr(request, "request_id", "") if request else ""

    if response is None:
        logger.exception("Unhandled API exception", exc_info=exc)
        return Response(
            error_response("INTERNAL_SERVER_ERROR", "Internal server error.", request_id),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    detail = response.data.get("detail") if isinstance(response.data, dict) and "detail" in response.data else response.data
    response.data = error_response(_error_code(exc), _message(detail), request_id)
    return response
