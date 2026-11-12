"""Stateless Responses transport over the existing routing/admission lifecycle.

The accounting envelope is internal only. Native input/output items must never
be translated to Chat messages on the wire (opaque reasoning is not text).
"""
from copy import deepcopy
import json
import tempfile


def gateway_payload(payload):
    body = deepcopy(payload)
    messages = []
    items = body.get("input")
    if isinstance(items, str):
        messages.append({"role": "user", "content": items})
    else:
        for item in items or []:
            content = item.get("content")
            if item.get("type") == "message":
                if isinstance(content, list):
                    parts = []
                    for part in content:
                        if part.get("type") in {"input_image", "image_url"}:
                            url = part["image_url"]
                            image = dict(url) if isinstance(url, dict) else {"url": url}
                            if "detail" in part:
                                image["detail"] = part["detail"]
                            parts.append({"type": "image_url", "image_url": image})
                        else:
                            parts.append({"type": "text", "text": part.get("text", part.get("refusal", ""))})
                    content = parts
                messages.append({"role": item.get("role", "user"), "content": content})
            else:
                messages.append({"role": "user", "content": json.dumps(item, ensure_ascii=False)})
    if body.get("instructions"):
        messages.insert(0, {"role": "system", "content": body["instructions"]})
    result = {"model": body["model"], "messages": messages, "stream": body.get("stream", False),
              "_nexus_operation": "responses", "_nexus_responses": body}
    for key in ("router_id", "metadata"):
        if key in body:
            result[key] = body[key]
    if "max_output_tokens" in body:
        result["max_tokens"] = body["max_output_tokens"]
    return result


def upstream_payload(payload, model):
    body = deepcopy(payload["_nexus_responses"])
    body.pop("router_id", None)
    body.pop("metadata", None)  # Nexus routing metadata is not provider metadata.
    body["model"] = model
    body["max_output_tokens"] = payload["max_tokens"]
    # Shared media authorization has already resolved each accounting message.
    images = iter(part["image_url"] for message in payload["messages"]
                  if isinstance(message.get("content"), list) for part in message["content"]
                  if part.get("type") == "image_url")
    if isinstance(body.get("input"), list):
        for item in body["input"]:
            if item.get("type") != "message" or not isinstance(item.get("content"), list):
                continue
            for part in item["content"]:
                if part.get("type") in {"input_image", "image_url"}:
                    image = next(images)
                    part.clear()
                    part.update(type="input_image", image_url=image["url"], detail=image.get("detail", "auto"))
                elif part.get("type") == "text":
                    part["type"] = "input_text"
    return body


def response_url(base_url):
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[:-len("/chat/completions")]
    return base if base.endswith("/responses") else base + "/responses"


def validate_response(raw):
    from .provider_adapters import ProviderClientError
    if not isinstance(raw, dict) or raw.get("object") != "response" or raw.get("status") not in ("completed", "incomplete") or not isinstance(raw.get("output"), list) or raw.get("error"):
        raise ProviderClientError("Provider did not return a completed native Responses result.",
                                  error_code="PROVIDER_RESPONSES_INVALID", safe_to_retry=False, affects_health=False)
    return raw


def stream_chunks(*, request, payload):
    from rest_framework.exceptions import APIException
    from .services import stream_chat_completions
    from .integration import gateway_integration
    from .sse_protocol import encode_json, encode_data
    from .stream_framing import iter_sse_data, StreamFramingError, StreamEncodingError, StreamLimits
    source = stream_chat_completions(request=request, payload=payload)
    events = gateway_integration().response_events(events=source)
    terminal = None
    try:
        for event in events:
            if event.error_code:
                yield encode_json({"type": "error", "code": event.error_code, "message": event.error_message,
                                   "details": event.error_details}, event="error")
                return
            if event.raw:
                if event.raw.get("type") in {"response.completed", "response.incomplete"}:
                    # Encode before settlement, deliver after settlement. SDKs
                    # may close immediately on a terminal event; that must not
                    # abandon the accounting generator's final transaction.
                    terminal = tempfile.SpooledTemporaryFile(max_size=StreamLimits.current().memory, mode="w+", encoding="utf-8")
                    for chunk in iter_sse_data(event.raw, event=event.raw["type"]):
                        terminal.write(chunk)
                else:
                    yield from iter_sse_data(event.raw, event=event.raw.get("type"))
        if terminal is not None:
            terminal.seek(0)
            while chunk := terminal.read(StreamLimits.current().write // 4):
                yield chunk
    except APIException as exc:
        from .openai_compat import openai_error
        error = openai_error(exc=exc, detail=exc.detail)["error"]
        yield encode_json({**error, "type": "error"}, event="error")
    except (StreamFramingError, OSError) as exc:
        if isinstance(exc, OSError):
            exc = StreamEncodingError(code="GATEWAY_STREAM_BUFFER_UNAVAILABLE",
                                      message="Gateway stream buffering failed. The request was not replayed.")
        # Fail the existing admission/reservation; never replay an inference.
        try:
            source.throw(exc)
        except (StopIteration, StreamFramingError):
            pass
        yield encode_json({"type": "error", "code": exc.code, "message": str(exc)}, event="error")
    finally:
        if terminal is not None:
            terminal.close()
        events.close()
        source.close()
    yield encode_data("[DONE]")
