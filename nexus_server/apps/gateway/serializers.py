from __future__ import annotations

import ipaddress
import uuid
from urllib.parse import urlparse

from django.conf import settings
from rest_framework import serializers


class StrictRequestSerializer(serializers.Serializer):
    """Never silently discard an option the caller expects to take effect."""

    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise serializers.ValidationError({name: "Unsupported parameter." for name in sorted(unknown)})
        return super().to_internal_value(data)


class ReasoningEffortField(serializers.CharField):
    def to_internal_value(self, data):
        from .model_metadata import effort_token
        if not effort_token(data):
            raise serializers.ValidationError("Expected a short reasoning effort identifier.")
        return data


def validate_output_format(value, *, responses=False):
    kind = value.get("type")
    if kind not in ("text", "json_object", "json_schema"):
        raise serializers.ValidationError("Use text, json_object, or json_schema.")
    spec = value if responses else value.get("json_schema", {})
    allowed = {"type", "name", "description", "schema", "strict"} if responses and kind == "json_schema" else {"type", "json_schema"} if kind == "json_schema" else {"type"}
    if set(value) - allowed:
        raise serializers.ValidationError("Unsupported output format parameter.")
    if kind == "json_schema":
        if not isinstance(spec, dict) or not isinstance(spec.get("name"), str) or not spec["name"] or not isinstance(spec.get("schema"), dict):
            raise serializers.ValidationError("json_schema requires a name and schema object.")
        if "strict" in spec and not isinstance(spec["strict"], bool):
            raise serializers.ValidationError("strict must be a boolean.")
        if set(spec) - {"type", "name", "description", "schema", "strict"}:
            raise serializers.ValidationError("Unsupported JSON Schema format option.")
        from jsonschema import Draft202012Validator, SchemaError
        try:
            Draft202012Validator.check_schema(spec["schema"])
        except SchemaError:
            raise serializers.ValidationError("Invalid JSON Schema.") from None
    return value


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
        result = {"type": "text", "text": text}
        if "prompt_cache_breakpoint" in part:
            validator = PromptCacheBreakpointSerializer(data=part["prompt_cache_breakpoint"])
            validator.is_valid(raise_exception=True)
            result["prompt_cache_breakpoint"] = validator.validated_data
        return result

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
        if "prompt_cache_breakpoint" in part:
            validator = PromptCacheBreakpointSerializer(data=part["prompt_cache_breakpoint"])
            validator.is_valid(raise_exception=True)
            result["prompt_cache_breakpoint"] = validator.validated_data
        return result


class ToolFunctionCallSerializer(StrictRequestSerializer):
    name = serializers.CharField(max_length=128)
    arguments = serializers.CharField(allow_blank=True, trim_whitespace=False)


class ToolCallSerializer(StrictRequestSerializer):
    id = serializers.CharField(max_length=255)
    type = serializers.ChoiceField(choices=["function"])
    function = ToolFunctionCallSerializer()


class ChatMessageSerializer(StrictRequestSerializer):
    role = serializers.CharField(max_length=32)
    content = ChatMessageContentField(required=False, allow_null=True)
    tool_calls = ToolCallSerializer(many=True, required=False, allow_empty=True, allow_null=True)
    tool_call_id = serializers.CharField(max_length=255, required=False)
    name = serializers.CharField(max_length=128, required=False)
    reasoning_content = serializers.CharField(required=False, allow_null=True, allow_blank=True, trim_whitespace=False)
    reasoning = serializers.JSONField(required=False, allow_null=True)
    reasoning_details = serializers.ListField(child=serializers.DictField(), required=False, allow_null=True)
    refusal = serializers.CharField(required=False, allow_null=True, allow_blank=True, trim_whitespace=False)
    annotations = serializers.ListField(child=serializers.DictField(), required=False, allow_null=True)
    function_call = ToolFunctionCallSerializer(required=False, allow_null=True)
    audio = serializers.JSONField(required=False, allow_null=True)

    def validate_audio(self, value):
        if value is not None:
            raise serializers.ValidationError("Audio history is not supported by this endpoint.")
        return value

    def to_internal_value(self, data):
        # OpenAI assistant tool-call messages may use either null or empty content.
        if isinstance(data, dict) and data.get("role") == "assistant" and (data.get("tool_calls") or data.get("function_call")) and data.get("content") == "":
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
        if attrs.get("content") is None and not (role == "assistant" and (attrs.get("tool_calls") or attrs.get("function_call") or attrs.get("refusal"))):
            raise serializers.ValidationError("content is required unless the assistant calls a tool.")
        return attrs


class ToolDefinitionFunctionSerializer(StrictRequestSerializer):
    name = serializers.CharField(max_length=128)
    description = serializers.CharField(required=False, allow_blank=True)
    parameters = serializers.DictField(required=False)
    strict = serializers.BooleanField(required=False)


class ToolDefinitionSerializer(StrictRequestSerializer):
    type = serializers.ChoiceField(choices=["function"])
    function = ToolDefinitionFunctionSerializer()


class PromptCacheOptionsSerializer(StrictRequestSerializer):
    mode = serializers.ChoiceField(choices=["implicit", "explicit"], required=False)
    ttl = serializers.ChoiceField(choices=["30m", "12h"], required=False)
    prewarm = serializers.BooleanField(required=False)


class PromptCacheBreakpointSerializer(StrictRequestSerializer):
    mode = serializers.ChoiceField(choices=["explicit"])
    ttl = serializers.ChoiceField(choices=["30m", "12h"], required=False)


class CacheRequestSerializer(StrictRequestSerializer):
    prompt_cache_key = serializers.CharField(required=False, allow_null=True, max_length=256, trim_whitespace=False)
    prompt_cache_retention = serializers.ChoiceField(choices=["in_memory", "24h"], required=False, allow_null=True)
    prompt_cache_options = PromptCacheOptionsSerializer(required=False, allow_null=True)

    def validate(self, attrs):
        if attrs.get("prompt_cache_retention") is not None and attrs.get("prompt_cache_options") is not None:
            raise serializers.ValidationError("Use prompt_cache_retention or prompt_cache_options, not both.")
        return attrs


class ChatCompletionRequestSerializer(CacheRequestSerializer):
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
    reasoning_effort = ReasoningEffortField(required=False)
    max_completion_tokens = serializers.IntegerField(required=False, min_value=1)
    top_p = serializers.FloatField(required=False, min_value=0, max_value=1)
    stop = serializers.JSONField(required=False, allow_null=True)
    verbosity = serializers.ChoiceField(choices=["low", "medium", "high"], required=False)
    frequency_penalty = serializers.FloatField(required=False, min_value=-2, max_value=2)
    presence_penalty = serializers.FloatField(required=False, min_value=-2, max_value=2)
    logprobs = serializers.BooleanField(required=False)
    top_logprobs = serializers.IntegerField(required=False, min_value=0, max_value=20)
    logit_bias = serializers.DictField(child=serializers.FloatField(min_value=-100, max_value=100), required=False)
    service_tier = serializers.ChoiceField(choices=["auto", "default", "flex", "priority"], required=False)
    user = serializers.CharField(required=False, max_length=255)

    def validate_response_format(self, value):
        return validate_output_format(value)

    def validate_stop(self, value):
        if value is None or isinstance(value, str) or (isinstance(value, list) and 1 <= len(value) <= 4 and all(isinstance(item, str) for item in value)):
            return value
        raise serializers.ValidationError("stop must be a string or up to four strings.")

    def validate(self, attrs):
        if "max_tokens" in attrs and "max_completion_tokens" in attrs:
            raise serializers.ValidationError("Specify only one output token limit.")
        return super().validate(attrs)

    def validate_tool_choice(self, value):
        if isinstance(value, str) and value in {"auto", "none", "required"}:
            return value
        if isinstance(value, dict) and value.get("type") == "function":
            function = value.get("function")
            if isinstance(function, dict) and isinstance(function.get("name"), str) and function["name"]:
                return {"type": "function", "function": {"name": function["name"]}}
        raise serializers.ValidationError("Use auto, none, required, or a named function.")


def validate_client_tools(tools, depth=0):
    """Preserve Codex client tools without enabling provider-hosted execution."""
    if not isinstance(tools, list) or len(tools) > 256 or depth > 2:
        raise serializers.ValidationError("Invalid client tool list.")
    for tool in tools:
        if not isinstance(tool, dict):
            raise serializers.ValidationError("Tool definitions must be objects.")
        kind = tool.get("type")
        if kind == "namespace":
            allowed = {"type", "name", "description", "tools"}
            validate_client_tools(tool.get("tools"), depth + 1)
        elif kind == "custom":
            allowed = {"type", "name", "description", "format"}
            fmt = tool.get("format", {"type": "text"})
            if not isinstance(fmt, dict) or fmt.get("type") not in ("text", "grammar"):
                raise serializers.ValidationError("Invalid custom tool format.")
            if fmt["type"] == "grammar" and (set(fmt) != {"type", "syntax", "definition"} or fmt.get("syntax") not in ("lark", "regex") or not isinstance(fmt.get("definition"), str)):
                raise serializers.ValidationError("Invalid custom tool grammar.")
            if fmt["type"] == "text" and set(fmt) != {"type"}:
                raise serializers.ValidationError("Invalid text tool format.")
        elif kind == "function":
            allowed = {"type", "name", "description", "parameters", "strict"}
            validator = ToolDefinitionFunctionSerializer(data={key: val for key, val in tool.items() if key != "type"})
            validator.is_valid(raise_exception=True)
        else:
            raise serializers.ValidationError("Only client-executed function and custom tools are supported.")
        if set(tool) - allowed or not isinstance(tool.get("name"), str) or not 1 <= len(tool["name"]) <= 128:
            raise serializers.ValidationError("Invalid client tool definition.")
        if "description" in tool and not isinstance(tool["description"], str):
            raise serializers.ValidationError("Invalid tool description.")
    return tools


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
        if not isinstance(item_type, str):
            raise serializers.ValidationError("Input item type must be a string.")
        if item_type == "additional_tools":
            if set(item) - {"type", "id", "role", "tools"} or item.get("role") != "developer":
                raise serializers.ValidationError("Invalid additional tools item.")
            validate_client_tools(item.get("tools"))
            return dict(item)
        if item_type in {"function_call", "function_call_output", "custom_tool_call", "custom_tool_call_output", "reasoning"}:
            fields = {
                "function_call": {"type", "id", "call_id", "name", "namespace", "arguments", "status"},
                "function_call_output": {"type", "id", "call_id", "output", "status"},
                "custom_tool_call": {"type", "id", "call_id", "name", "namespace", "input", "status"},
                "custom_tool_call_output": {"type", "id", "call_id", "output", "status"},
                "reasoning": {"type", "id", "summary", "content", "encrypted_content", "status"},
            }[item_type]
            if set(item) - fields:
                raise serializers.ValidationError("Unsupported input item field.")
            required = {"function_call": ("call_id", "name", "arguments"), "function_call_output": ("call_id",), "custom_tool_call": ("call_id", "name", "input"), "custom_tool_call_output": ("call_id",), "reasoning": ("id",)}[item_type]
            if any(not isinstance(item.get(key), str) for key in required):
                raise serializers.ValidationError("Input item is missing required string fields.")
            if item_type == "reasoning" and not isinstance(item.get("summary", []), list):
                raise serializers.ValidationError("Reasoning summary must be an array.")
            if item_type.endswith("_output"):
                output = item.get("output")
                if not isinstance(output, str):
                    # Text-only arrays here; images must use the message media
                    # authorization pipeline before they can reach a provider.
                    if not isinstance(output, list) or not output or len(output) > 64 or any(
                        not isinstance(part, dict) or part.get("type") not in ("input_text", "output_text", "text")
                        or not isinstance(part.get("text"), str) for part in output
                    ):
                        raise serializers.ValidationError("Tool output must be text or text parts.")
            if "namespace" in item and not isinstance(item["namespace"], str):
                raise serializers.ValidationError("Tool namespace must be a string.")
            return dict(item)
        if item_type not in {"message", "input_message"}:
            raise serializers.ValidationError("Unsupported Responses input item type.")
        role = item.get("role") or "user"
        if role not in ("system", "developer", "user", "assistant"):
            raise serializers.ValidationError("message input item role is not supported.")
        content = item.get("content")
        if content is None:
            raise serializers.ValidationError("message input item content is required.")
        if set(item) - {"type", "role", "content", "id", "status", "phase"}:
            raise serializers.ValidationError("Unsupported message field.")
        if "phase" in item and item["phase"] not in (None, "commentary", "final_answer"):
            raise serializers.ValidationError("Invalid assistant message phase.")
        validate_response_content(content)
        # Retain output message IDs, annotations and encrypted reasoning history.
        return {**item, "type": "message", "role": role}


class ResponseCreateRequestSerializer(CacheRequestSerializer):
    model = serializers.CharField(max_length=255)
    input = ResponseInputField()
    instructions = serializers.CharField(required=False, allow_blank=True)
    temperature = serializers.FloatField(required=False)
    max_output_tokens = serializers.IntegerField(required=False, min_value=1)
    stream = serializers.BooleanField(required=False, default=False)
    stream_options = serializers.DictField(required=False)
    router_id = serializers.CharField(max_length=128, required=False, allow_blank=True)
    metadata = serializers.DictField(required=False)
    store = serializers.BooleanField(required=False, default=False)
    tools = serializers.ListField(required=False)
    tool_choice = serializers.JSONField(required=False)
    parallel_tool_calls = serializers.BooleanField(required=False, default=True)
    reasoning = serializers.DictField(required=False)
    text = serializers.DictField(required=False)
    top_p = serializers.FloatField(required=False, min_value=0, max_value=1)
    include = serializers.ListField(child=serializers.ChoiceField(choices=["reasoning.encrypted_content", "message.output_text.logprobs"]), required=False)
    truncation = serializers.ChoiceField(choices=["auto", "disabled"], required=False)
    service_tier = serializers.ChoiceField(choices=["auto", "default", "flex", "priority"], required=False)
    client_metadata = serializers.DictField(child=serializers.CharField(max_length=16384, allow_blank=True), required=False)

    def validate_store(self, value):
        if value:
            raise serializers.ValidationError("Stored provider state is not supported. Use store=false and send input history.")
        return value

    def validate_reasoning(self, value):
        from .model_metadata import effort_token
        if set(value) - {"effort", "summary", "context"} or ("effort" in value and not effort_token(value["effort"])) or ("summary" in value and value["summary"] not in ("auto", "concise", "detailed", None)) or ("context" in value and value["context"] != "all_turns"):
            raise serializers.ValidationError("Invalid reasoning effort or summary.")
        return value

    def validate_text(self, value):
        if set(value) - {"format", "verbosity"} or ("verbosity" in value and value["verbosity"] not in ("low", "medium", "high")):
            raise serializers.ValidationError("Invalid text options.")
        if "format" in value:
            if not isinstance(value["format"], dict):
                raise serializers.ValidationError("format must be an object.")
            validate_output_format(value["format"], responses=True)
        return value

    def validate_tools(self, value):
        return validate_client_tools(value)

    def validate_tool_choice(self, value):
        if isinstance(value, str) and value in {"auto", "none", "required"}:
            return value
        if isinstance(value, dict) and set(value) == {"type", "name"} and value.get("type") == "function" and isinstance(value.get("name"), str) and value["name"]:
            return value
        raise serializers.ValidationError("Use auto, none, required, or a named function.")


def validate_response_content(content):
    if isinstance(content, str):
        if not content:
            raise serializers.ValidationError("message content must not be empty.")
        return content
    if not isinstance(content, list) or not content:
        raise serializers.ValidationError("message content must be a string or content part array.")
    if len(content) > int(getattr(settings, "NEXUS_MULTIMODAL_MAX_CONTENT_PARTS", 64)):
        raise serializers.ValidationError("Too many message content parts.")
    validated = []
    image_count = 0
    for part in content:
        if not isinstance(part, dict):
            raise serializers.ValidationError("message content parts must be objects.")
        if "prompt_cache_breakpoint" in part:
            validator = PromptCacheBreakpointSerializer(data=part["prompt_cache_breakpoint"])
            validator.is_valid(raise_exception=True)
        part_type = part.get("type")
        if not isinstance(part_type, str):
            raise serializers.ValidationError("Content part type must be a string.")
        if part_type == "refusal" and isinstance(part.get("refusal"), str):
            validated.append(dict(part))
            continue
        if part_type in {"input_text", "output_text", "text"}:
            text = part.get("text")
            if not isinstance(text, str) or not text:
                raise serializers.ValidationError("text content parts require non-empty text.")
            validated.append({"type": "text", "text": text})
            continue
        if part_type in {"input_image", "image_url"}:
            image_count += 1
            if image_count > int(getattr(settings, "NEXUS_MULTIMODAL_MAX_IMAGE_PARTS", 8)):
                raise serializers.ValidationError("Too many image parts.")
            image_url = part.get("image_url")
            if isinstance(image_url, str):
                image_url = {"url": image_url}
            if not isinstance(image_url, dict):
                raise serializers.ValidationError("image content parts require an image_url.")
            url = image_url.get("url")
            if not isinstance(url, str) or not url:
                raise serializers.ValidationError("image_url.url is required.")
            validate_multimodal_url(url)
            if part.get("detail", image_url.get("detail", "auto")) not in ("auto", "low", "high"):
                raise serializers.ValidationError("Invalid image detail.")
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
