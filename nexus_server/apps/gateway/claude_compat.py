from __future__ import annotations

import time
import uuid
from typing import Any

from django.http import StreamingHttpResponse
from rest_framework import exceptions, status
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.renderers import EventStreamRenderer
from apps.common.models import SoftDeleteModel
from apps.deployments.models import ModelGroup
from apps.common.request_context import get_tenant_from_request

from .services import chat_completions, stream_chat_completions
from .serializers import ChatMessageContentField


class ClaudeCompatibilityError(exceptions.APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Invalid Claude-compatible request."
    default_code = "invalid_request_error"


class ClaudeMessagesView(APIView):
    renderer_classes = [JSONRenderer, EventStreamRenderer]

    def handle_exception(self, exc):  # noqa: D401 - DRF hook.
        status_code = getattr(exc, "status_code", status.HTTP_400_BAD_REQUEST)
        detail = getattr(exc, "detail", str(exc))
        message = anthropic_error_message(detail)
        error_type = getattr(exc, "default_code", None) or getattr(exc, "code", None) or "api_error"
        return Response(
            {"type": "error", "error": {"type": str(error_type), "message": message}},
            status=status_code,
        )

    def post(self, request):
        apply_api_key_context(request)
        anthropic_payload = request.data
        if not isinstance(anthropic_payload, dict):
            raise ClaudeCompatibilityError("Request body must be a JSON object.")
        nexus_payload = anthropic_to_openai_payload(anthropic_payload)
        if nexus_payload.get("stream"):
            response = StreamingHttpResponse(
                anthropic_stream_chunks(
                    request=request,
                    payload=nexus_payload,
                    requested_model=str(anthropic_payload.get("model") or nexus_payload["model"]),
                ),
                content_type="text/event-stream",
            )
            response["Cache-Control"] = "no-cache"
            response["X-Accel-Buffering"] = "no"
            return response
        raw = chat_completions(request=request, payload=nexus_payload)
        return Response(openai_to_anthropic_message(raw=raw, requested_model=str(anthropic_payload.get("model") or nexus_payload["model"])))


class ClaudeModelsView(APIView):
    renderer_classes = [JSONRenderer]

    def handle_exception(self, exc):  # noqa: D401 - DRF hook.
        status_code = getattr(exc, "status_code", status.HTTP_400_BAD_REQUEST)
        detail = getattr(exc, "detail", str(exc))
        return Response(
            {"type": "error", "error": {"type": "api_error", "message": anthropic_error_message(detail)}},
            status=status_code,
        )

    def get(self, request):
        from .integration import gateway_integration
        return gateway_integration().claude_model_catalog(view=self, request=request)


def anthropic_to_openai_payload(payload: dict[str, Any]) -> dict[str, Any]:
    model = payload.get("model")
    if not isinstance(model, str) or not model:
        raise ClaudeCompatibilityError("model is required.")
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ClaudeCompatibilityError("messages must be a non-empty array.")
    converted_messages = []
    system = payload.get("system")
    if isinstance(system, str) and system:
        converted_messages.append({"role": "system", "content": system})
    elif isinstance(system, list) and system:
        converted_messages.append({"role": "system", "content": convert_anthropic_content(system)})
    for message in messages:
        if not isinstance(message, dict):
            raise ClaudeCompatibilityError("messages must contain objects.")
        role = message.get("role")
        if role not in {"user", "assistant"}:
            raise ClaudeCompatibilityError("message role must be user or assistant.")
        converted_messages.append({"role": role, "content": convert_anthropic_content(message.get("content", ""))})
    result: dict[str, Any] = {
        "model": model,
        "messages": converted_messages,
        "stream": bool(payload.get("stream", False)),
    }
    if "temperature" in payload:
        result["temperature"] = payload["temperature"]
    max_tokens = payload.get("max_tokens")
    if isinstance(max_tokens, int) and max_tokens > 0:
        result["max_tokens"] = max_tokens
    router_id = payload.get("router_id")
    if isinstance(router_id, str) and router_id:
        result["router_id"] = router_id
    return result


def apply_api_key_context(request) -> None:
    api_key = getattr(request, "api_key", None)
    if api_key is None:
        return
    if not getattr(request, "tenant_id", ""):
        request.tenant_id = str(api_key.tenant_id)
    if not getattr(request, "project_id", "") and api_key.project_id:
        request.project_id = str(api_key.project_id)


def convert_anthropic_content(content: Any):
    if isinstance(content, str):
        if not content:
            raise ClaudeCompatibilityError("message content must not be empty.")
        return content
    if not isinstance(content, list) or not content:
        raise ClaudeCompatibilityError("message content must be a string or content block array.")
    converted = []
    for block in content:
        if not isinstance(block, dict):
            raise ClaudeCompatibilityError("content blocks must be objects.")
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if not isinstance(text, str) or not text:
                raise ClaudeCompatibilityError("text blocks require text.")
            converted.append({"type": "text", "text": text})
        elif block_type == "image":
            converted.append(convert_anthropic_image(block))
        else:
            raise ClaudeCompatibilityError(f"unsupported content block type: {block_type}")
    # Conversion alone is not validation. Apply the same image URL, payload
    # size and content-count boundary as Chat before any streaming or dispatch.
    try:
        return ChatMessageContentField().run_validation(converted)
    except exceptions.ValidationError as exc:
        raise ClaudeCompatibilityError(exc.detail) from exc


def convert_anthropic_image(block: dict[str, Any]) -> dict[str, Any]:
    source = block.get("source")
    if not isinstance(source, dict):
        raise ClaudeCompatibilityError("image blocks require source.")
    source_type = source.get("type")
    if source_type == "base64":
        media_type = source.get("media_type")
        data = source.get("data")
        if not isinstance(media_type, str) or not media_type.startswith("image/") or not isinstance(data, str) or not data:
            raise ClaudeCompatibilityError("base64 image source requires image media_type and data.")
        return {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{data}"}}
    if source_type == "url":
        url = source.get("url")
        if not isinstance(url, str) or not url:
            raise ClaudeCompatibilityError("url image source requires url.")
        return {"type": "image_url", "image_url": {"url": url}}
    raise ClaudeCompatibilityError("unsupported image source type.")


def openai_to_anthropic_message(*, raw: dict[str, Any], requested_model: str) -> dict[str, Any]:
    choice = first_choice(raw)
    content = choice.get("message", {}).get("content", "") if isinstance(choice.get("message"), dict) else ""
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    return {
        "id": str(raw.get("id") or f"msg_{uuid.uuid4().hex}"),
        "type": "message",
        "role": "assistant",
        "model": str(raw.get("model") or requested_model),
        "content": anthropic_response_content(content),
        "stop_reason": stop_reason(choice.get("finish_reason")),
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
        },
        "created_at": int(raw.get("created") or time.time()),
    }


def anthropic_stream_chunks(*, request, payload: dict[str, Any], requested_model: str):
    message_id = f"msg_{uuid.uuid4().hex}"
    model = requested_model
    output_tokens = 0
    input_tokens = 0
    finish_reason = "end_turn"
    yield anthropic_sse(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": model,
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        },
    )
    yield anthropic_sse("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
    try:
        for event in stream_chat_completions(request=request, payload=payload):
            if event.error_code:
                yield anthropic_sse(
                    "error",
                    {
                        "type": "error",
                        "error": {"type": event.error_code.lower(), "message": event.error_message},
                    },
                )
                return
            if event.raw and event.raw.get("model"):
                model = str(event.raw.get("model"))
            if event.text_delta:
                yield anthropic_sse(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": event.text_delta},
                    },
                )
            if event.finish_reason:
                finish_reason = stop_reason(event.finish_reason)
            if event.usage:
                input_tokens = int(event.usage.get("prompt_tokens") or event.usage.get("input_tokens") or input_tokens)
                output_tokens = int(event.usage.get("completion_tokens") or event.usage.get("output_tokens") or output_tokens)
    except exceptions.APIException as exc:
        yield anthropic_sse(
            "error",
            {
                "type": "error",
                "error": {"type": str(getattr(exc, "default_code", "api_error")).lower(), "message": anthropic_error_message(exc.detail)},
            },
        )
        return
    yield anthropic_sse("content_block_stop", {"type": "content_block_stop", "index": 0})
    yield anthropic_sse(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": finish_reason, "stop_sequence": None},
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        },
    )
    yield anthropic_sse("message_stop", {"type": "message_stop"})


def anthropic_sse(event: str, payload: dict[str, Any]) -> str:
    import json

    return f"event: {event}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


def first_choice(raw: dict[str, Any]) -> dict[str, Any]:
    choices = raw.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        return choices[0]
    return {}


def anthropic_response_content(content: Any) -> list[dict[str, str]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        text = "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("type") == "text")
        return [{"type": "text", "text": text}]
    return [{"type": "text", "text": ""}]


def stop_reason(value: Any) -> str:
    if value == "length":
        return "max_tokens"
    if value in {"tool_calls", "function_call"}:
        return "tool_use"
    return "end_turn"


def anthropic_error_message(detail: Any) -> str:
    if isinstance(detail, dict):
        return "; ".join(f"{key}: {anthropic_error_message(value)}" for key, value in detail.items())
    if isinstance(detail, list):
        return "; ".join(anthropic_error_message(value) for value in detail)
    return str(detail)
