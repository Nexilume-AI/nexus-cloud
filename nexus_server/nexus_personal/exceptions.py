"""Personal HTTP errors: retain protocol errors, never log unexpected payloads."""
import logging

from rest_framework.response import Response
from rest_framework.views import exception_handler

from apps.common.exceptions import unified_exception_handler
from apps.common.responses import error_response


logger = logging.getLogger(__name__)


def personal_exception_handler(exc, context):
    if exception_handler(exc, context) is not None:
        # Preserve the shared status/headers and field-level validation contract.
        # Only unhandled exceptions use the separate, secret-safe logging path.
        return unified_exception_handler(exc, context)
    # Exception text, causes, traceback and request fields may contain upstream
    # credentials. Do not attach them as logging args, exc_info or extra fields.
    logger.error("Unhandled Personal API exception: INTERNAL_SERVER_ERROR")
    request_id = getattr(context.get("request"), "request_id", "")
    return Response(error_response("INTERNAL_SERVER_ERROR", "Internal server error.", request_id), status=500)
