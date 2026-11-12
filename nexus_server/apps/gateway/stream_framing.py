"""Bound downstream writes and SSE lines without truncating model output.

An HTTP write, an SSE data line and a logical SSE event are different units.
Known OpenAI deltas can become smaller events. Opaque metadata must instead
remain in one event, with JSON whitespace at token boundaries across data lines.
"""
from __future__ import annotations

import codecs
import json
import os
import tempfile
from dataclasses import dataclass

from django.conf import settings

from .sse_protocol import encode_data_bytes, encode_json


MAX_FRAME_BYTES = 64 * 1024
# A transport write is not a line limit. Opaque strings (including signatures)
# must fit intact; consumers need their own compatible event/line budgets.
MAX_LINE_BYTES = 64 * 1024 * 1024
MAX_EVENT_BYTES = 64 * 1024 * 1024
# Even JSON control-character escaping fits within one frame at this size.
TEXT_CHARS = 8192
_ENVELOPE_KEYS = {"id", "object", "created", "model", "system_fingerprint", "service_tier"}
_TEXT_KEYS = {"content", "reasoning_content", "reasoning", "refusal"}


def _limit(name, default, ceiling):
    value = getattr(settings, name, os.environ.get(name, default))
    try:
        return min(ceiling, max(1, int(value)))
    except (ValueError, TypeError):
        return default


@dataclass(frozen=True)
class StreamLimits:
    write: int
    line: int
    event: int
    buffer: int
    memory: int

    @classmethod
    def current(cls):
        event = _limit("NEXUS_GATEWAY_SSE_MAX_EVENT_BYTES", MAX_EVENT_BYTES, MAX_EVENT_BYTES)
        line = min(event, _limit("NEXUS_GATEWAY_SSE_MAX_LINE_BYTES", MAX_LINE_BYTES, MAX_LINE_BYTES))
        return cls(
            max(4, _limit("NEXUS_GATEWAY_SSE_WRITE_BYTES", MAX_FRAME_BYTES, MAX_FRAME_BYTES)),
            line, event,
            _limit("NEXUS_GATEWAY_SSE_MAX_BUFFER_BYTES", 128 * 1024 * 1024, 128 * 1024 * 1024),
            _limit("NEXUS_GATEWAY_SSE_MEMORY_BYTES", 1024 * 1024, 1024 * 1024),
        )


class StreamFramingError(Exception):
    code = "PROVIDER_STREAM_FRAME_TOO_LARGE"
    error_code = code
    safe_to_retry = False
    affects_health = False
    status_code = None
    retry_after_seconds = None

    def __init__(self, *, unit="JSON token", size=0, limit=MAX_LINE_BYTES - 7):
        self.details = {"origin": "nexus", "stage": "downstream_encoding", "unit": unit,
                        "encoded_bytes": size, "limit_bytes": limit, "field_path": "$",
                        "retry_safe": False}
        self.error_details = self.details
        self.message = (
            f"Provider stream {unit} exceeds the safe encoding limit "
            f"({size} bytes; limit {limit} bytes). "
            "The value cannot be split without changing its meaning. The request was not replayed."
        )
        super().__init__(self.message)


class StreamEncodingError(StreamFramingError):
    def __init__(self, *, code, message):
        self.code = self.error_code = code
        self.message = message
        self.details = self.error_details = {"origin": "nexus", "stage": "downstream_encoding", "retry_safe": False}
        Exception.__init__(self, message)


def sse_data(payload: dict) -> str:
    return encode_json(payload)


def _json_lines(payload, limits):
    # iterencode emits complete JSON tokens; adding whitespace BETWEEN them is
    # lossless, including for nested arrays, signatures and provider extensions.
    parts, line_size = [], 0
    encoder = json.JSONEncoder(ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    for token in encoder.iterencode(payload):
        token_size = len(token.encode("utf-8"))
        if token_size > limits.line - 7:  # data prefix + LF
            raise StreamFramingError(size=token_size, limit=limits.line - 7)
        if parts and line_size + token_size > min(limits.write, limits.line) - 7:
            yield "".join(parts)
            parts, line_size = [], 0
        parts.append(token)
        line_size += token_size
    if parts:
        yield "".join(parts)


def _safe_field_path(payload, limit, path="$", depth=0):
    # Never return arbitrary provider keys, IDs or values in diagnostics.
    names = _ENVELOPE_KEYS | _TEXT_KEYS | {"choices", "delta", "tool_calls", "function",
        "arguments", "reasoning_details", "data", "signature", "encrypted_content", "logprobs", "usage"}
    if isinstance(payload, str):
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > limit:
            return path
    elif depth < 32 and isinstance(payload, (dict, list)):
        for key, value in (payload.items() if isinstance(payload, dict) else enumerate(payload)):
            suffix = ("." + key if key in names else ".<extension>") if isinstance(key, str) else "[]"
            found = _safe_field_path(value, limit, path + suffix, depth + 1)
            if found:
                return found
    return None


def _spool_event(buffer, payload, limits, event=None):
    start = buffer.tell()
    try:
        for line in _json_lines(payload, limits):
            # Let sse-starlette encode the field; omit only its blank event
            # terminator so successive fields remain one logical SSE event.
            wire = memoryview(encode_data_bytes(line, event=event))[:-1]
            event = None
            size = buffer.tell() - start + len(wire) + 1
            if size > limits.event:
                raise StreamFramingError(unit="event", size=size, limit=limits.event)
            if buffer.tell() + len(wire) + 1 > limits.buffer:
                raise StreamFramingError(unit="event batch", size=buffer.tell() + len(wire) + 1, limit=limits.buffer)
            buffer.write(wire)
        buffer.write(b"\n")
    except StreamFramingError as exc:
        if exc.details["unit"] == "JSON token":
            exc.details["field_path"] = _safe_field_path(payload, limits.line - 7) or "$"
        raise
    return buffer.tell() - start


def _text_parts(text):
    if not text:
        yield ""
    for offset in range(0, len(text), TEXT_CHARS):
        yield text[offset:offset + TEXT_CHARS]


def _function_parts(function):
    if not isinstance(function, dict) or not isinstance(function.get("arguments"), str):
        yield function
        return
    metadata = {key: value for key, value in function.items() if key != "arguments"}
    for part in _text_parts(function["arguments"]):
        yield {**metadata, "arguments": part}
        metadata = {}


def _delta_parts(delta):
    for key, value in delta.items():
        if key in _TEXT_KEYS and isinstance(value, str):
            for part in _text_parts(value):
                yield {key: part}
        elif key == "function_call":
            for part in _function_parts(value):
                yield {key: part}
        elif key == "tool_calls" and isinstance(value, list):
            for position, call in enumerate(value):
                if not isinstance(call, dict):
                    yield {key: [call]}
                    continue
                index = call.get("index", position)
                if "function" not in call:
                    yield {key: [{**call, "index": index}]}
                    continue
                metadata = {k: v for k, v in call.items() if k not in {"index", "function"}}
                for part in _function_parts(call["function"]):
                    yield {key: [{**metadata, "index": index, "function": part}]}
                    # IDs/types/names must not be concatenated by SDK clients.
                    metadata = {}
        else:
            yield {key: value}


def _fragments(payload):
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not all(
        isinstance(choice, dict) and isinstance(choice.get("delta"), dict) for choice in choices
    ):
        # Usage-only or opaque provider events have no delta semantics to split.
        yield payload
        return
    envelope = {key: value for key, value in payload.items() if key in _ENVELOPE_KEYS}
    for position, choice in enumerate(choices):
        index = choice.get("index", position)
        for delta in _delta_parts(choice["delta"]):
            yield {**envelope, "choices": [{"index": index, "delta": delta, "finish_reason": None}]}
        # Finish and per-choice metadata follow all of this choice's deltas.
        tail = {key: value for key, value in choice.items() if key != "delta"}
        yield {**envelope, "choices": [{**tail, "index": index, "delta": {}}]}
    tail = {key: value for key, value in payload.items() if key not in _ENVELOPE_KEYS and key != "choices"}
    if tail:
        # In particular, usage is delivered once, never once per fragment.
        yield {**envelope, **tail, "choices": []}


def iter_sse_data(payload: dict, *, event=None):
    try:
        yield from _iter_sse_data(payload, event=event)
    except OSError as exc:
        raise StreamEncodingError(code="GATEWAY_STREAM_BUFFER_UNAVAILABLE",
            message="Gateway stream buffering failed. The request was not replayed.") from exc
    except (ValueError, RecursionError) as exc:
        raise StreamEncodingError(code="PROVIDER_STREAM_INVALID_DATA",
            message="Provider stream contains data that cannot be encoded as valid JSON. The request was not replayed.") from exc


def _iter_sse_data(payload: dict, *, event=None):
    limits = StreamLimits.current()
    # Measure incrementally, rather than allocate several full SSE/JSON copies.
    small = 8 + (len(event.encode("utf-8")) + 8 if event else 0)
    for token in json.JSONEncoder(ensure_ascii=False, separators=(',', ':'), allow_nan=False).iterencode(payload):
        small += len(token.encode("utf-8"))
        if small > min(limits.write, limits.line, limits.event, limits.buffer):
            break
    else:
        yield encode_json(payload, event=event) if event else sse_data(payload)
        return
    # Preflight the whole upstream event before exposing any of its fragments.
    # Spill encoded bytes to a private temporary file, closed on error/cancel.
    with tempfile.SpooledTemporaryFile(max_size=min(limits.memory, limits.buffer), mode="w+b") as buffer:
        lengths = [_spool_event(buffer, fragment, limits, event=event) for fragment in ([payload] if event else _fragments(payload))]
        buffer.seek(0)
        for length in lengths:
            decoder = codecs.getincrementaldecoder("utf-8")()
            while length:
                chunk = buffer.read(min(length, limits.write - 3 if length > limits.write else length))
                length -= len(chunk)
                text = decoder.decode(chunk, final=not length)
                # A decoder may carry up to 3 bytes from the preceding read.
                if len(text.encode("utf-8")) > limits.write:
                    for offset in range(0, len(text), limits.write // 4):
                        yield text[offset:offset + limits.write // 4]
                elif text:
                    yield text
