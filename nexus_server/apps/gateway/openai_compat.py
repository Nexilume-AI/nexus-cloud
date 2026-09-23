from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from typing import Any

from asgiref.sync import sync_to_async
from django.db import OperationalError
from django.http import StreamingHttpResponse
from rest_framework import exceptions, status
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.models import SoftDeleteModel
from apps.common.renderers import EventStreamRenderer
from apps.deployments.models import ModelGroup
from apps.routers.models import Router
from apps.common.request_context import get_tenant_from_request

from .serializers import ChatCompletionRequestSerializer, ResponseCreateRequestSerializer
from .services import chat_completions, router_model_catalog, stream_chat_completions
from .views import openai_stream_chunks


class OpenAICompatibilityError(exceptions.APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Invalid OpenAI-compatible request."
    default_code = "invalid_request_error"


class OpenAIChatCompletionsView(APIView):
    renderer_classes = [JSONRenderer, EventStreamRenderer]

    def handle_exception(self, exc):  # noqa: D401 - DRF hook.
        return openai_exception_response(exc)

    def post(self, request):
        apply_api_key_context(request)
        serializer = ChatCompletionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        if payload.get("stream"):
            from django.http import StreamingHttpResponse

            response = StreamingHttpResponse(
                openai_stream_chunks(request=request, payload=payload),
                content_type="text/event-stream",
            )
            response["Cache-Control"] = "no-cache"
            response["X-Accel-Buffering"] = "no"
            return response
        return Response(chat_completions(request=request, payload=payload))


class OpenAIResponsesView(APIView):
    renderer_classes = [JSONRenderer, EventStreamRenderer]

    def handle_exception(self, exc):  # noqa: D401 - DRF hook.
        return openai_exception_response(exc)

    def post(self, request):
        apply_api_key_context(request)
        serializer = ResponseCreateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        response_payload = serializer.validated_data
        chat_payload = responses_payload_to_chat_payload(response_payload)
        chat_payload["_nexus_operation"] = "responses"
        if response_payload.get("stream"):
            response = StreamingHttpResponse(
                async_stream_iterator(responses_stream_chunks(request=request, payload=chat_payload)),
                content_type="text/event-stream",
            )
            response["Cache-Control"] = "no-cache"
            response["X-Accel-Buffering"] = "no"
            return response
        return Response(chat_completion_to_response(chat_completions(request=request, payload=chat_payload)))


class OpenAIModelsView(APIView):
    renderer_classes = [JSONRenderer]

    def handle_exception(self, exc):  # noqa: D401 - DRF hook.
        return openai_exception_response(exc)

    def get(self, request):
        from .integration import gateway_integration
        return gateway_integration().openai_model_catalog(view=self, request=request)


def responses_payload_to_chat_payload(payload: dict[str, Any]) -> dict[str, Any]:
    messages = responses_input_to_messages(payload.get("input"))
    instructions = payload.get("instructions") or ""
    if instructions:
        messages.insert(0, {"role": "system", "content": instructions})
    chat_payload: dict[str, Any] = {
        "model": payload["model"],
        "messages": messages,
        "stream": bool(payload.get("stream")),
    }
    if "temperature" in payload:
        chat_payload["temperature"] = payload["temperature"]
    if "max_output_tokens" in payload:
        chat_payload["max_tokens"] = payload["max_output_tokens"]
    if "stream_options" in payload:
        chat_payload["stream_options"] = payload["stream_options"]
    if payload.get("router_id"):
        chat_payload["router_id"] = payload["router_id"]
    if payload.get("metadata"):
        chat_payload["metadata"] = payload["metadata"]
    return chat_payload


def responses_input_to_messages(input_value: Any) -> list[dict[str, Any]]:
    if isinstance(input_value, str):
        return [{"role": "user", "content": input_value}]
    messages = []
    for item in input_value or []:
        role = "system" if item.get("role") == "developer" else item.get("role", "user")
        messages.append({"role": role, "content": response_content_to_chat_content(item.get("content"))})
    return messages


def response_content_to_chat_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    text_parts = [part["text"] for part in content or [] if part.get("type") == "text"]
    image_parts = [part for part in content or [] if part.get("type") == "image_url"]
    if image_parts:
        return [{"type": "text", "text": "\n".join(text_parts) or "Please analyze this input."}, *image_parts]
    return "\n".join(text_parts)


def chat_completion_to_response(raw: dict[str, Any]) -> dict[str, Any]:
    choice = (raw.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    text = response_text_from_message(message)
    created = int(raw.get("created") or time.time())
    usage = response_usage(raw.get("usage") or {})
    return {
        "id": f"resp_{uuid.uuid4().hex}",
        "object": "response",
        "created_at": created,
        "status": "completed",
        "background": False,
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "max_output_tokens": None,
        "model": raw.get("model") or "",
        "output": [
            {
                "id": f"msg_{uuid.uuid4().hex}",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "output_text": text,
        "parallel_tool_calls": True,
        "previous_response_id": None,
        "reasoning": None,
        "store": False,
        "temperature": None,
        "text": {"format": {"type": "text"}},
        "tool_choice": "auto",
        "tools": [],
        "top_p": None,
        "truncation": "disabled",
        "usage": usage,
    }


def response_text_from_message(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict) and part.get("type") in {"text", "output_text"}).strip()
    return ""


def response_usage(usage: dict[str, Any]) -> dict[str, int]:
    input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": 0},
    }


def responses_stream_chunks(*, request, payload: dict[str, Any]):
    response_id = f"resp_{uuid.uuid4().hex}"
    message_id = f"msg_{uuid.uuid4().hex}"
    model = payload.get("model") or ""
    created = int(time.time())
    text_parts: list[str] = []
    usage: dict[str, Any] = {}
    yield response_sse(
        "response.created",
        {"response": response_stream_object(response_id=response_id, created=created, model=model, status="in_progress")},
    )
    yield response_sse(
        "response.in_progress",
        {"response": response_stream_object(response_id=response_id, created=created, model=model, status="in_progress")},
    )
    yield response_sse(
        "response.output_item.added",
        {"response_id": response_id, "output_index": 0, "item": {"id": message_id, "type": "message", "status": "in_progress", "role": "assistant", "content": []}},
    )
    yield response_sse(
        "response.content_part.added",
        {"response_id": response_id, "item_id": message_id, "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}},
    )
    from .integration import gateway_integration
    for event in gateway_integration().response_events(events=stream_chat_completions(request=request, payload=payload)):
        if event.error_code:
            yield response_sse(
                "response.failed",
                {
                    "response": response_stream_object(
                        response_id=response_id,
                        created=created,
                        model=model,
                        status="failed",
                        error={"code": event.error_code, "message": event.error_message},
                    ),
                },
            )
            yield "data: [DONE]\n\n"
            return
        if event.text_delta:
            text_parts.append(event.text_delta)
            yield response_sse(
                "response.output_text.delta",
                {"response_id": response_id, "item_id": message_id, "output_index": 0, "content_index": 0, "delta": event.text_delta},
            )
        if event.usage:
            usage = event.usage
        if event.raw and not usage:
            raw_usage = event.raw.get("usage") if isinstance(event.raw, dict) else None
            if isinstance(raw_usage, dict):
                usage = raw_usage
    text = "".join(text_parts)
    yield response_sse("response.output_text.done", {"response_id": response_id, "item_id": message_id, "output_index": 0, "content_index": 0, "text": text})
    yield response_sse(
        "response.content_part.done",
        {"response_id": response_id, "item_id": message_id, "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": text, "annotations": []}},
    )
    yield response_sse(
        "response.output_item.done",
        {
            "response_id": response_id,
            "output_index": 0,
            "item": {"id": message_id, "type": "message", "status": "completed", "role": "assistant", "content": [{"type": "output_text", "text": text, "annotations": []}]},
        },
    )
    yield response_sse(
        "response.completed",
        {
            "response": response_stream_object(
                response_id=response_id,
                created=created,
                model=model,
                status="completed",
                output=[
                    {"id": message_id, "type": "message", "status": "completed", "role": "assistant", "content": [{"type": "output_text", "text": text, "annotations": []}]}
                ],
                output_text=text,
                usage=response_usage(usage),
            ),
        },
    )
    yield "data: [DONE]\n\n"


def response_stream_object(
    *,
    response_id: str,
    created: int,
    model: str,
    status: str,
    output: list[dict[str, Any]] | None = None,
    output_text: str = "",
    usage: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": response_id,
        "object": "response",
        "created_at": created,
        "status": status,
        "background": False,
        "error": error,
        "incomplete_details": None,
        "instructions": None,
        "max_output_tokens": None,
        "model": model,
        "output": output or [],
        "output_text": output_text,
        "parallel_tool_calls": True,
        "previous_response_id": None,
        "reasoning": None,
        "store": False,
        "temperature": None,
        "text": {"format": {"type": "text"}},
        "tool_choice": "auto",
        "tools": [],
        "top_p": None,
        "truncation": "disabled",
        "usage": usage,
    }


def response_sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps({'type': event, **payload}, separators=(',', ':'))}\n\n"


_STREAM_DONE = object()


def next_stream_chunk(iterator: Iterator[str]) -> str | object:
    try:
        return next(iterator)
    except StopIteration:
        return _STREAM_DONE


async def async_stream_iterator(iterator: Iterator[str]):
    while True:
        chunk = await sync_to_async(next_stream_chunk, thread_sensitive=True)(iterator)
        if chunk is _STREAM_DONE:
            break
        yield chunk


def apply_api_key_context(request) -> None:
    api_key = getattr(request, "api_key", None)
    if api_key is None:
        return
    if not getattr(request, "tenant_id", ""):
        request.tenant_id = str(api_key.tenant_id)
    if not getattr(request, "project_id", "") and api_key.project_id:
        request.project_id = str(api_key.project_id)


def openai_exception_response(exc: Exception) -> Response:
    # A database outage is not a malformed client request. Do not expose database
    # connection details, and leave retry/idempotency decisions to the caller.
    if isinstance(exc, OperationalError):
        return Response(
            {"error": {"message": "Database temporarily unavailable.",
                       "type": "api_error", "code": "DATABASE_UNAVAILABLE"}},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": "2"},
        )
    status_code = getattr(exc, "status_code", status.HTTP_400_BAD_REQUEST)
    detail = getattr(exc, "detail", str(exc))
    return Response(openai_error(exc=exc, detail=detail), status=status_code)


def openai_error(*, exc: Exception, detail: Any) -> dict[str, Any]:
    return {
        "error": {
            "message": openai_error_message(detail),
            "type": openai_error_type(exc),
            "code": openai_error_code(exc),
        }
    }


def openai_error_code(exc: Exception) -> str:
    code = getattr(exc, "default_code", None) or getattr(exc, "code", None) or exc.__class__.__name__
    return str(code).upper()


def openai_error_type(exc: Exception) -> str:
    status_code = getattr(exc, "status_code", status.HTTP_400_BAD_REQUEST)
    if status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}:
        return "authentication_error"
    if status_code == status.HTTP_404_NOT_FOUND:
        return "invalid_request_error"
    if status_code >= 500:
        return "api_error"
    return "invalid_request_error"


def openai_error_message(detail: Any) -> str:
    if isinstance(detail, dict):
        return "; ".join(f"{key}: {openai_error_message(value)}" for key, value in detail.items())
    if isinstance(detail, list):
        return "; ".join(openai_error_message(value) for value in detail)
    return str(detail)
