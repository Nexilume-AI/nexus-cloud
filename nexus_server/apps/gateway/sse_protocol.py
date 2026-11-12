"""Library-backed SSE codec over an already-authorized Provider connection.

No HTTP client is created here: DNS pinning, TLS, deadlines and retry decisions
remain with provider_http. The only wire inspection is a pre-decoder byte budget;
httpx-sse owns SSE fields, Unicode decoding, multiline data and event dispatch.
"""
from contextlib import closing
import codecs
import re

import httpx
from httpx_sse import EventSource
from sse_starlette import JSONServerSentEvent, ServerSentEvent


class SSESizeError(ValueError):
    def __init__(self, kind, limit, size=None):
        self.kind, self.limit = kind, limit
        self.size = size
        super().__init__(f"SSE {kind} exceeds {limit} bytes")


class _WireBudget:
    """Count UTF-8 bytes before the library buffers them, not SSE field values.

    Newlines count as one byte after CRLF normalization. Empty lines reset the
    event budget, including comment-only heartbeats. No JSON or field parsing.
    """
    def __init__(self, limit, event_limit=None):
        self.limit = limit
        self.event_limit = limit if event_limit is None else event_limit
        self.line = self.event = 0
        self.trailing_cr = False

    def check(self, chunk):
        if self.trailing_cr and chunk.startswith(b"\n"):
            chunk = chunk[1:]
        if not chunk:
            self.trailing_cr = False
            return
        self.trailing_cr = chunk.endswith(b"\r")
        for part in re.split(rb"(\r\n|\r|\n)", chunk):
            if part in (b"\r\n", b"\r", b"\n"):
                self.event += 1
                if self.line + 1 > self.limit:
                    raise SSESizeError("line", self.limit, self.line + 1)
                if self.event > self.event_limit:
                    raise SSESizeError("event", self.event_limit, self.event)
                if not self.line:
                    self.event = 0
                self.line = 0
            else:
                self.line += len(part)
                self.event += len(part)
                if self.line > self.limit:
                    raise SSESizeError("line", self.limit, self.line)
                if self.event > self.event_limit:
                    raise SSESizeError("event", self.event_limit, self.event)


class _ProviderBytes(httpx.SyncByteStream):
    def __init__(self, response, limit, event_limit=None):
        self.response, self.limit = response, limit
        self.event_limit = event_limit

    def __iter__(self):
        budget = _WireBudget(self.limit, self.event_limit)
        decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
        reader = getattr(self.response, "read1", None) or self.response.read
        while chunk := reader(min(65536, self.limit + 1)):
            budget.check(chunk)
            # HTTPX's text decoding can replace invalid UTF-8. Reject corruption
            # explicitly instead of changing model text or tool arguments.
            decoder.decode(chunk)
            yield chunk
        decoder.decode(b"", final=True)
        # Compatibility: some providers terminate the last JSON/[DONE] line at
        # EOF without an empty line. Let the library dispatch it, not custom code.
        yield b"\n\n"

    def close(self):
        close = getattr(self.response, "close", None)
        if close is not None:
            close()


def iter_events(response, *, limit, event_limit=None):
    # This Response only adapts bytes; it cannot open sockets or replay a POST.
    with closing(httpx.Response(200, headers={"Content-Type": "text/event-stream"},
            stream=_ProviderBytes(response, limit, event_limit), default_encoding="utf-8-sig")) as bridge:
        yield from EventSource(bridge).iter_sse()


def encode_data(data):
    return encode_data_bytes(data).decode("utf-8")


def encode_data_bytes(data, *, event=None):
    return ServerSentEvent(data=data, event=event, sep="\n").encode()


def encode_json(payload, *, event=None):
    return JSONServerSentEvent(data=payload, event=event, sep="\n").encode().decode("utf-8")
