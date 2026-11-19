from __future__ import annotations

import logging
import re
import uuid
import threading
from contextlib import closing

from django.http import StreamingHttpResponse
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.renderers import EventStreamRenderer, UnifiedJSONRenderer
from apps.common.streaming import AsyncStreamBridge

from .serializers import ChatCompletionRequestSerializer
from .services import chat_completions, stream_chat_completions
from .stream_framing import StreamFramingError, iter_sse_data, sse_data
from .sse_protocol import encode_data

logger = logging.getLogger(__name__)


class ChatCompletionsView(APIView):
    renderer_classes = [UnifiedJSONRenderer, EventStreamRenderer]

    def post(self, request):
        serializer = ChatCompletionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        if payload.get("stream"):
            return chat_stream_response(request=request, payload=payload)
        return Response(chat_completions(request=request, payload=payload))


def chat_stream_response(*, request, payload):
    request._nexus_stream_cancelled = threading.Event()
    chunks = openai_stream_chunks(request=request, payload=payload)
    # ASGI eagerly collects synchronous iterators, delaying every token until
    # inference ends. Keep WSGI synchronous and bridge ASGI with bounded buffering.
    if hasattr(getattr(request, "_request", request), "scope"):
        chunks = AsyncStreamBridge(chunks, cancel=request._nexus_stream_cancelled.set, heartbeat_seconds=10)
    response = StreamingHttpResponse(chunks, content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


def openai_stream_chunks(*, request, payload):
    events = None
    output_started = False
    request_id = str(getattr(request, "request_id", ""))
    if not re.fullmatch(r"req_[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f-]{27}", request_id):
        request_id = "req_" + uuid.uuid4().hex

    def failure(code, message, details=None):
        details = {**(details or {}), "request_id": request_id, "output_started": output_started}
        logger.warning("Gateway stream failure code=%s details=%s", code, details)
        return sse_data({"error": {"code": code, "message": message, "details": details}})

    try:
        events = stream_chat_completions(request=request, payload=payload)
        for event in events:
            if event.error_code:
                yield failure(event.error_code, event.error_message, event.error_details)
                break
            if event.raw is not None:
                with closing(iter_sse_data(event.raw)) as chunks:
                    for chunk in chunks:
                        output_started = True
                        yield chunk
    except StreamFramingError as exc:
        # Notify the billing/routing generator of delivery failure before closing
        # it. This records a failed attempt and never resumes the upstream POST.
        notify = getattr(events, "throw", None)
        if notify is not None:
            try:
                notify(exc)
            except (StreamFramingError, StopIteration):
                pass
        close = getattr(events, "close", None)
        if close is not None:
            close()
        yield failure(exc.code, exc.message, exc.details)
    except exceptions.APIException as exc:
        yield failure(str(getattr(exc, "default_code", "GATEWAY_ERROR")).upper(),
                      str(exc.detail), getattr(exc, "error_details", None))
    finally:
        # Release upstream sockets and reservations on cancellation or framing
        # failure even when another object retains the generator reference.
        close = getattr(events, "close", None)
        if close is not None:
            close()
    yield encode_data("[DONE]")
