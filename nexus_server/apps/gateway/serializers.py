from __future__ import annotations

import ipaddress
import uuid
from urllib.parse import urlparse

from django.conf import settings
from rest_framework import serializers


class ChatMessageContentField(serializers.Field):
    def to_internal_value(self, data):
        if isinstance(data, str):
            if data == "":
                raise serializers.ValidationError("content must not be empty.")
            return data
        if isinstance(data, list):
            return self.validate_parts(data)
        raise serializers.ValidationError("content must be a string or an OpenAI-compatible content part array.")

    def to_representation(self, value):
        return value

    def validate_parts(self, parts: list) -> list[dict]:
        max_parts = int(getattr(settings, "NEXUS_MULTIMODAL_MAX_CONTENT_PARTS", 64))
        max_images = int(getattr(settings, "NEXUS_MULTIMODAL_MAX_IMAGE_PARTS", 8))
        if not parts:
            raise serializers.ValidationError("content part array must not be empty.")
        if len(parts) > max_parts:
            raise serializers.ValidationError(f"content part array may contain at most {max_parts} parts.")
        image_count = 0
        validated = []
        for part in parts:
            if not isinstance(part, dict):
                raise serializers.ValidationError("content parts must be objects.")
            part_type = part.get("type")
            if part_type == "text":
                validated.append(self.validate_text_part(part))
                continue
            if part_type == "image_url":
                image_count += 1
                if image_count > max_images:
                    raise serializers.ValidationError(f"content may contain at most {max_images} image_url parts.")
                validated.append(self.validate_image_url_part(part))
                continue
            raise serializers.ValidationError("unsupported content part type.")
        return validated

    def validate_text_part(self, part: dict) -> dict:
        text = part.get("text")
        if not isinstance(text, str) or text == "":
            raise serializers.ValidationError("text content parts require non-empty text.")
        return {"type": "text", "text": text}

    def validate_image_url_part(self, part: dict) -> dict:
        image_url = part.get("image_url")
        if not isinstance(image_url, dict):
            raise serializers.ValidationError("image_url content parts require an image_url object.")
        url = image_url.get("url")
        if not isinstance(url, str) or not url:
            raise serializers.ValidationError("image_url.url is required.")
        validate_multimodal_url(url)
        result = {"type": "image_url", "image_url": dict(image_url)}
        if "detail" in image_url:
            detail = image_url.get("detail")
            if detail not in {"auto", "low", "high"}:
                raise serializers.ValidationError("image_url.detail must be auto, low, or high.")
            result["image_url"]["detail"] = detail
        return result


class ToolFunctionCallSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=128)
    arguments = serializers.CharField(allow_blank=True, trim_whitespace=False)


class ToolCallSerializer(serializers.Serializer):
    id = serializers.CharField(max_length=255)
    type = serializers.ChoiceField(choices=["function"])
    function = ToolFunctionCallSerializer()


class ChatMessageSerializer(serializers.Serializer):
    role = serializers.CharField(max_length=32)
    content = ChatMessageContentField(required=False, allow_null=True)
    tool_calls = ToolCallSerializer(many=True, required=False, allow_empty=True, allow_null=True)
    tool_call_id = serializers.CharField(max_length=255, required=False)
    name = serializers.CharField(max_length=128, required=False)

    def to_internal_value(self, data):
        # OpenAI assistant tool-call messages may use either null or empty content.
        if isinstance(data, dict) and data.get("role") == "assistant" and data.get("tool_calls") and data.get("content") == "":
            data = {**data, "content": None}
        return super().to_internal_value(data)

    def validate(self, attrs):
        role = attrs["role"]
        if attrs.get("tool_calls") and role != "assistant":
            raise serializers.ValidationError("Only assistant messages may contain tool_calls.")
        if role == "tool" and not attrs.get("tool_call_id"):
            raise serializers.ValidationError("Tool messages require tool_call_id.")
        if role != "tool" and attrs.get("tool_call_id"):
            raise serializers.ValidationError("tool_call_id belongs to tool messages.")
        if attrs.get("content") is None and not (role == "assistant" and attrs.get("tool_calls")):
            raise serializers.ValidationError("content is required unless the assistant calls a tool.")
        return attrs


class ToolDefinitionFunctionSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=128)
    description = serializers.CharField(required=False, allow_blank=True)
    parameters = serializers.DictField(required=False)
    strict = serializers.BooleanField(required=False)


class ToolDefinitionSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=["function"])
    function = ToolDefinitionFunctionSerializer()


class ChatCompletionRequestSerializer(serializers.Serializer):
    model = serializers.CharField(max_length=255)
    messages = ChatMessageSerializer(many=True)
    temperature = serializers.FloatField(required=False)
    max_tokens = serializers.IntegerField(required=False, min_value=1)
    stream = serializers.BooleanField(required=False, default=False)
    stream_options = serializers.DictField(required=False)
    router_id = serializers.CharField(max_length=128, required=False, allow_blank=True)
    metadata = serializers.DictField(required=False)
    tools = ToolDefinitionSerializer(many=True, required=False)
    tool_choice = serializers.JSONField(required=False)
    parallel_tool_calls = serializers.BooleanField(required=False)
    response_format = serializers.DictField(required=False)
    seed = serializers.IntegerField(required=False)
    enable_thinking = serializers.BooleanField(required=False)
    thinking_budget = serializers.IntegerField(required=False, min_value=128, max_value=32768)

    def validate_tool_choice(self, value):
        if isinstance(value, str) and value in {"auto", "none", "required"}:
            return value
        if isinstance(value, dict) and value.get("type") == "function":
            function = value.get("function")
            if isinstance(function, dict) and isinstance(function.get("name"), str) and function["name"]:
                return {"type": "function", "function": {"name": function["name"]}}
        raise serializers.ValidationError("Use auto, none, required, or a named function.")


class ResponseInputField(serializers.Field):
    def to_internal_value(self, data):
        if isinstance(data, str):
            if not data:
                raise serializers.ValidationError("input must not be empty.")
            return data
        if isinstance(data, list):
            if not data:
                raise serializers.ValidationError("input array must not be empty.")
            return [self.validate_input_item(item) for item in data]
        raise serializers.ValidationError("input must be a string or a Responses API input item array.")

    def to_representation(self, value):
        return value

    def validate_input_item(self, item: dict) -> dict:
        if not isinstance(item, dict):
            raise serializers.ValidationError("input items must be objects.")
        item_type = item.get("type") or "message"
        if item_type not in {"message", "input_message"}:
            raise serializers.ValidationError("only message input items are supported.")
        role = item.get("role") or "user"
        if role not in {"system", "developer", "user", "assistant"}:
            raise serializers.ValidationError("message input item role is not supported.")
        content = item.get("content")
        if content is None:
            raise serializers.ValidationError("message input item content is required.")
        return {"type": "message", "role": role, "content": validate_response_content(content)}


class ResponseCreateRequestSerializer(serializers.Serializer):
    model = serializers.CharField(max_length=255)
    input = ResponseInputField()
    instructions = serializers.CharField(required=False, allow_blank=True)
    temperature = serializers.FloatField(required=False)
    max_output_tokens = serializers.IntegerField(required=False, min_value=1)
    stream = serializers.BooleanField(required=False, default=False)
    stream_options = serializers.DictField(required=False)
    router_id = serializers.CharField(max_length=128, required=False, allow_blank=True)
    metadata = serializers.DictField(required=False)
    store = serializers.BooleanField(required=False, default=True)
    tools = serializers.ListField(required=False)
    tool_choice = serializers.JSONField(required=False)
    parallel_tool_calls = serializers.BooleanField(required=False, default=True)


def validate_response_content(content):
    if isinstance(content, str):
        if not content:
            raise serializers.ValidationError("message content must not be empty.")
        return content
    if not isinstance(content, list) or not content:
        raise serializers.ValidationError("message content must be a string or content part array.")
    validated = []
    for part in content:
        if not isinstance(part, dict):
            raise serializers.ValidationError("message content parts must be objects.")
        part_type = part.get("type")
        if part_type in {"input_text", "output_text", "text"}:
            text = part.get("text")
            if not isinstance(text, str) or not text:
                raise serializers.ValidationError("text content parts require non-empty text.")
            validated.append({"type": "text", "text": text})
            continue
        if part_type in {"input_image", "image_url"}:
            image_url = part.get("image_url")
            if isinstance(image_url, str):
                image_url = {"url": image_url}
            if not isinstance(image_url, dict):
                raise serializers.ValidationError("image content parts require an image_url.")
            url = image_url.get("url")
            if not isinstance(url, str) or not url:
                raise serializers.ValidationError("image_url.url is required.")
            validate_multimodal_url(url)
            validated.append({"type": "image_url", "image_url": dict(image_url)})
            continue
        raise serializers.ValidationError("unsupported Responses API content part type.")
    return validated


def validate_multimodal_url(url: str) -> None:
    data_url_limit = int(getattr(settings, "NEXUS_MULTIMODAL_DATA_URL_MAX_BYTES", 2_000_000))
    if url.startswith("data:"):
        if not url.startswith("data:image/"):
            raise serializers.ValidationError("only image data URLs are supported.")
        if len(url.encode("utf-8")) > data_url_limit:
            raise serializers.ValidationError("image data URL exceeds the configured size limit.")
        return
    if url.startswith("nexus-media://"):
        parsed_media = urlparse(url)
        try:
            uuid.UUID(parsed_media.netloc)
        except (TypeError, ValueError) as exc:
            raise serializers.ValidationError("nexus-media URL requires a media asset id.") from exc
        return

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise serializers.ValidationError("image_url.url must use http, https, or data:image.")
    if not parsed.hostname:
        raise serializers.ValidationError("image_url.url requires a host.")
    if is_local_hostname(parsed.hostname):
        raise serializers.ValidationError("local image URLs are not allowed.")


def is_local_hostname(hostname: str) -> bool:
    lowered = hostname.lower()
    if lowered in {"localhost", "localhost.localdomain"} or lowered.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(lowered.strip("[]"))
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local or address.is_reserved
