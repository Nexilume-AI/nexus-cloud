from __future__ import annotations

import json

from django.http import StreamingHttpResponse
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.renderers import EventStreamRenderer, UnifiedJSONRenderer

from .serializers import ChatCompletionRequestSerializer
from .services import chat_completions, stream_chat_completions


class ChatCompletionsView(APIView):
    renderer_classes = [UnifiedJSONRenderer, EventStreamRenderer]

    def post(self, request):
        serializer = ChatCompletionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        if payload.get("stream"):
            response = StreamingHttpResponse(
                openai_stream_chunks(request=request, payload=payload),
                content_type="text/event-stream",
            )
            response["Cache-Control"] = "no-cache"
            response["X-Accel-Buffering"] = "no"
            return response
        return Response(chat_completions(request=request, payload=payload))


def openai_stream_chunks(*, request, payload):
    try:
        for event in stream_chat_completions(request=request, payload=payload):
            if event.error_code:
                yield sse_data({"error": {"code": event.error_code, "message": event.error_message}})
                break
            if event.raw is not None:
                yield sse_data(event.raw)
    except exceptions.APIException as exc:
        yield sse_data({"error": {"code": str(getattr(exc, "default_code", "GATEWAY_ERROR")).upper(), "message": str(exc.detail)}})
    yield "data: [DONE]\n\n"


def sse_data(payload: dict) -> str:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
