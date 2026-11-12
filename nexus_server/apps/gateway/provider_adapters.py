from __future__ import annotations

import json
import os
import ssl
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from collections.abc import Iterator
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

from django.conf import settings

from apps.common.crypto import decrypt_secret
from .provider_http import ProviderEndpointRejected, ProviderTransportError, open_provider_url
from .sse_protocol import SSESizeError, iter_events


@dataclass
class ProviderResponse:
    raw: dict[str, Any]
    request_tokens: int
    response_tokens: int
    total_tokens: int
    model: str
    latency_ms: int
    cached_input_tokens: int = 0
    reasoning_output_tokens: int = 0
    operation: str = "chat.completions"
    image_count: int = 0


@dataclass
class ProviderStreamEvent:
    raw: dict[str, Any] | None = None
    text_delta: str = ""
    finish_reason: str = ""
    usage: dict[str, Any] | None = None
    error_code: str = ""
    error_message: str = ""
    done: bool = False
    error_details: dict[str, Any] | None = None


@dataclass
class ProviderHealthResult:
    healthy: bool
    degraded: bool
    reason: str
    latency_ms: int


class ProviderClientError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str = "PROVIDER_REQUEST_FAILED",
        retry_after_seconds: int | None = None,
        safe_to_retry: bool | None = None,
        affects_health: bool = True,
        error_details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(redact_provider_error(message))
        self.status_code = status_code
        self.error_code = error_code
        self.retry_after_seconds = retry_after_seconds
        self.safe_to_retry = safe_to_retry
        self.affects_health = affects_health
        self.error_details = error_details


def transport_client_error(exc) -> ProviderClientError:
    if isinstance(exc, ssl.SSLError):
        return ProviderClientError("Provider TLS verification or negotiation failed.",
                                   error_code="PROVIDER_TLS_FAILED", safe_to_retry=False)
    before_send = isinstance(exc, ProviderTransportError) and exc.safe_to_retry
    timed_out = exc.timed_out if isinstance(exc, ProviderTransportError) else isinstance(exc, TimeoutError)
    if before_send:
        return ProviderClientError("Provider connection could not be established before dispatch.",
            error_code="PROVIDER_CONNECT_TIMEOUT" if timed_out else "PROVIDER_CONNECTION_FAILED", safe_to_retry=True)
    return ProviderClientError(
        "Provider response timed out; the dispatched request was not automatically replayed." if timed_out else
        "Provider connection was interrupted after dispatch; the result is uncertain and was not replayed.",
        error_code="PROVIDER_TIMEOUT" if timed_out else "PROVIDER_REQUEST_FAILED",
        safe_to_retry=False, affects_health=not timed_out)


def _response_limit(name: str, default: int) -> int:
    # Django overrides win; shared adapters also support operator environment
    # settings in distributions which do not explicitly declare these knobs.
    return min(64 * 1024 * 1024, max(1, int(getattr(settings, name, os.environ.get(name, default)))))


@contextmanager
def _buffer_response(response, *, line: bool = False):
    """Spill large bodies/lines to a private, automatically deleted temp file.

    The memory threshold is NOT a response limit. The final JSON object still
    needs parsing memory, so retain a separate operator-controlled hard bound.
    No partial tool arguments are emitted and no inference is replayed here.
    """
    setting = "NEXUS_PROVIDER_SSE_MAX_LINE_BYTES" if line else "NEXUS_PROVIDER_RESPONSE_MAX_BYTES"
    limit = _response_limit(setting, 64 * 1024 * 1024)
    memory_limit = _response_limit("NEXUS_PROVIDER_RESPONSE_MEMORY_BYTES", 1024 * 1024)
    reader = response.readline if line else response.read
    with tempfile.SpooledTemporaryFile(max_size=min(memory_limit, limit), mode="w+b") as buffer:
        size = 0
        while True:
            chunk = reader(min(64 * 1024, limit - size + 1))
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                kind = "streaming line" if line else "response body"
                raise ProviderClientError(
                    f"Provider {kind} exceeds the configured hard limit of {limit} bytes ({setting}). "
                    "Reduce the requested output or ask the operator to review this limit. "
                    "The request was not automatically replayed.",
                    error_code="PROVIDER_RESPONSE_TOO_LARGE",
                )
            buffer.write(chunk)
            if line and chunk.endswith(b"\n"):
                break
        buffer.seek(0)
        yield buffer


class BaseProviderAdapter:
    def images(self, *, deployment, operation, payload, files=()):
        raise ProviderClientError("Provider adapter does not support Images API.", error_code="MODEL_OPERATION_UNSUPPORTED")

    def chat_completions(self, *, deployment, payload: dict[str, Any]) -> ProviderResponse:
        raise NotImplementedError

    def stream_chat_completions(self, *, deployment, payload: dict[str, Any]) -> Iterator[ProviderStreamEvent]:
        raise NotImplementedError

    def check_health(self, *, deployment) -> ProviderHealthResult:
        raise NotImplementedError


class OpenAICompatibleAdapter(BaseProviderAdapter):
    def images(self, *, deployment, operation, payload, files=()):
        suffix = {"images.generate": "generations", "images.edit": "edits", "images.variation": "variations"}[operation]
        account = deployment.provider_account
        if account is None:
            raise ProviderClientError("Provider account is required.")
        base = (deployment.endpoint or account.url).rstrip("/")
        fields = {key: value for key, value in payload.items() if key not in {"router_id", "response_format"} and not key.startswith("_")}
        fields["model"] = deployment.model
        if operation == "images.variation":
            fields.pop("quality", None)
        # Output format is normalized by Nexus. Do not send the unsupported
        # response_format option to GPT Image providers; accept URL or base64.
        headers = self.headers(account=account)
        if files:
            boundary = "nexus-" + uuid.uuid4().hex
            parts = []
            for name, value in fields.items():
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
            for name, data, mime in files:
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="image"\r\nContent-Type: {mime}\r\n\r\n'.encode() + data + b"\r\n")
            body = b"".join(parts) + f"--{boundary}--\r\n".encode()
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        else:
            body = json.dumps(fields).encode()
        try:
            with open_provider_url(
                base + "/images/" + suffix,
                method="POST",
                body=body,
                headers=headers,
                timeout=float(getattr(settings, "NEXUS_IMAGE_TIMEOUT_SECONDS", 180)),
                allow_private=managed_provider_endpoint(deployment=deployment, url=base),
                allow_http=managed_provider_endpoint(deployment=deployment, url=base),
            ) as response:
                limit = int(getattr(settings, "NEXUS_IMAGE_RESPONSE_MAX_BYTES", 60 * 1024 * 1024))
                encoded = response.read(limit + 1)
                if len(encoded) > limit:
                    raise ValueError()
                raw = json.loads(encoded)
                if not isinstance(raw, dict):
                    raise ValueError()
                return raw
        except HTTPError as exc:
            # Provider errors may echo prompts, credentials or input data.
            raise ProviderClientError("Provider rejected the image request.", status_code=exc.code, error_code="IMAGE_PROVIDER_REJECTED") from exc
        except ProviderEndpointRejected as exc:
            raise endpoint_rejected_error(exc) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ProviderClientError("Image provider result is unknown; this request will not be automatically retried.", error_code="IMAGE_RESULT_UNCERTAIN") from exc
        except (ValueError, TypeError) as exc:
            raise ProviderClientError("Provider returned an invalid or oversized image response.", error_code="IMAGE_RESPONSE_INVALID") from exc

    def check_health(self, *, deployment) -> ProviderHealthResult:
        started = time.monotonic()
        account = deployment.provider_account
        if account is None:
            return ProviderHealthResult(False, False, "Provider account is required.", elapsed_ms(started))
        base_url = deployment.endpoint or account.url
        parsed = urlparse(base_url)
        if parsed.hostname in getattr(settings, "NEXUS_GATEWAY_FAIL_HOSTS", {"fail.local", "fail.provider.local"}):
            return ProviderHealthResult(False, False, "Mock provider unavailable.", elapsed_ms(started))
        if parsed.hostname in settings.NEXUS_GATEWAY_MOCK_HOSTS:
            return ProviderHealthResult(True, False, "Mock provider healthy.", elapsed_ms(started))
        try:
            self._request_models(deployment=deployment, account=account, base_url=base_url)
            return ProviderHealthResult(True, False, "Provider models endpoint is reachable.", elapsed_ms(started))
        except ProviderClientError as exc:
            if exc.status_code in {404, 405}:
                from .capabilities import deployment_contract
                if "chat.completions" not in deployment_contract(deployment)["operations"]:
                    return ProviderHealthResult(False, False, "Provider catalog is unavailable; image operations are not used as billable health probes.", elapsed_ms(started))
                try:
                    self._request_minimal_chat(deployment=deployment, base_url=base_url)
                    return ProviderHealthResult(True, True, "Provider chat endpoint is reachable; models endpoint is unavailable.", elapsed_ms(started))
                except ProviderClientError as chat_exc:
                    return ProviderHealthResult(False, False, str(chat_exc), elapsed_ms(started))
            return ProviderHealthResult(False, False, str(exc), elapsed_ms(started))

    def chat_completions(self, *, deployment, payload: dict[str, Any]) -> ProviderResponse:
        started = time.monotonic()
        timing = getattr(deployment, '_nexus_latency', None)
        if timing is None:
            timing = {}; deployment._nexus_latency = timing
        account = deployment.provider_account
        if account is None:
            raise ProviderClientError("Provider account is required.", error_code="PROVIDER_ACCOUNT_NOT_FOUND")
        base_url = deployment.endpoint or account.url
        parsed = urlparse(base_url)
        if parsed.hostname in getattr(settings, "NEXUS_GATEWAY_FAIL_HOSTS", {"fail.local", "fail.provider.local"}):
            raise ProviderClientError("Mock provider unavailable.")
        if parsed.hostname in settings.NEXUS_GATEWAY_MOCK_HOSTS:
            return self._mock_response(deployment=deployment, payload=payload, started=started)

        try:
            with open_provider_url(
                self.request_url(base_url, payload),
                method="POST",
                body=json.dumps(self.chat_payload(deployment=deployment, payload=payload)).encode("utf-8"),
                headers=self.headers(account=account),
                timeout=float(getattr(settings, "NEXUS_PROVIDER_REQUEST_TIMEOUT_SECONDS", 60)),
                connect_timeout=float(getattr(settings, "NEXUS_PROVIDER_CONNECT_TIMEOUT_SECONDS", 10)),
                allow_private=managed_provider_endpoint(deployment=deployment, url=base_url),
                allow_http=managed_provider_endpoint(deployment=deployment, url=base_url),
                timing=timing,
            ) as response:
                with _buffer_response(response) as buffer:
                    raw = json.load(buffer)
        except HTTPError as exc:
            raise self.request_error(exc, payload) from exc
        except ProviderEndpointRejected as exc:
            raise endpoint_rejected_error(exc) from exc
        except (URLError, OSError) as exc:
            raise transport_client_error(exc) from exc
        except ProviderClientError:
            raise
        except Exception as exc:  # noqa: BLE001 - converted at gateway boundary.
            raise ProviderClientError("Provider returned an invalid response.") from exc
        finally:
            timing['provider_total_ms'] = round((time.monotonic() - started) * 1000, 2)
        if "_nexus_responses" in payload:
            from .responses_native import validate_response
            validate_response(raw)
        return build_response(raw=raw, model=deployment.model, started=started)

    def stream_chat_completions(self, *, deployment, payload: dict[str, Any]) -> Iterator[ProviderStreamEvent]:
        started = time.monotonic()
        timing = getattr(deployment, '_nexus_latency', None)
        if timing is None:
            timing = {}; deployment._nexus_latency = timing
        account = deployment.provider_account
        if account is None:
            raise ProviderClientError("Provider account is required.", error_code="PROVIDER_ACCOUNT_NOT_FOUND")
        base_url = deployment.endpoint or account.url
        parsed = urlparse(base_url)
        if parsed.hostname in getattr(settings, "NEXUS_GATEWAY_FAIL_HOSTS", {"fail.local", "fail.provider.local"}):
            raise ProviderClientError("Mock provider unavailable.")
        if parsed.hostname in settings.NEXUS_GATEWAY_MOCK_HOSTS:
            yield from self._mock_stream(deployment=deployment, payload=payload)
            return

        upstream_payload = self.chat_payload(deployment=deployment, payload=payload)
        upstream_payload["stream"] = True
        if "_nexus_responses" not in payload:
            stream_options = upstream_payload.get("stream_options")
            if not isinstance(stream_options, dict):
                stream_options = {}
            stream_options.setdefault("include_usage", True)
            upstream_payload["stream_options"] = stream_options
        try:
            with open_provider_url(
                self.request_url(base_url, payload),
                method="POST",
                body=json.dumps(upstream_payload).encode("utf-8"),
                headers=self.headers(account=account, stream=True),
                timeout=float(getattr(settings, "NEXUS_PROVIDER_REQUEST_TIMEOUT_SECONDS", 60)),
                connect_timeout=float(getattr(settings, "NEXUS_PROVIDER_CONNECT_TIMEOUT_SECONDS", 10)),
                allow_private=managed_provider_endpoint(deployment=deployment, url=base_url),
                allow_http=managed_provider_endpoint(deployment=deployment, url=base_url),
                timing=timing,
            ) as response:
                completed = False
                parser = iter_responses_sse if "_nexus_responses" in payload else iter_openai_sse
                for event in parser(response):
                    timing.setdefault('first_event_ms', round((time.monotonic() - started) * 1000, 2))
                    if event.text_delta:
                        timing.setdefault('first_text_ms', round((time.monotonic() - started) * 1000, 2))
                        timing['last_text_ms'] = round((time.monotonic() - started) * 1000, 2)
                    if event.raw and event.raw.get("error"):
                        error = event.raw["error"]
                        # Codes are provider-controlled too: only expose known
                        # diagnostic categories, never arbitrary message/data.
                        upstream_code = error.get("code") if isinstance(error, dict) else None
                        known = {"PROVIDER_STREAM_FRAME_TOO_LARGE", "PROVIDER_RESPONSE_TOO_LARGE",
                                 "PROVIDER_TIMEOUT", "context_length_exceeded", "rate_limit_exceeded"}
                        details = {"origin": "upstream", "stage": "upstream_event", "retry_safe": False}
                        if isinstance(upstream_code, str) and upstream_code in known:
                            details["upstream_code"] = upstream_code
                        raise ProviderClientError("Provider reported a streaming failure; the request was not replayed.",
                                                  error_code="PROVIDER_STREAM_FAILED", safe_to_retry=False,
                                                  affects_health=False, error_details=details)
                    completed = completed or event.done
                    yield event
                if not completed:
                    raise ProviderClientError("Provider stream ended without its completion marker; the request was not replayed.",
                                              error_code="PROVIDER_STREAM_INCOMPLETE", safe_to_retry=False)
        except HTTPError as exc:
            raise self.request_error(exc, payload) from exc
        except ProviderEndpointRejected as exc:
            raise endpoint_rejected_error(exc) from exc
        except (URLError, OSError) as exc:
            raise transport_client_error(exc) from exc
        except ProviderClientError:
            raise
        except Exception as exc:  # noqa: BLE001 - converted at gateway boundary.
            raise ProviderClientError("Provider returned an invalid streaming response.") from exc
        finally:
            timing['provider_total_ms'] = round((time.monotonic() - started) * 1000, 2)

    def request_url(self, base_url, payload):
        if "_nexus_responses" in payload:
            from .responses_native import response_url
            return response_url(base_url)
        return self.chat_completions_url(base_url)

    def request_error(self, exc, payload):
        if "_nexus_responses" in payload and exc.code in {404, 405, 501}:
            return ProviderClientError("This Source does not support native Responses. Use a compatible Source or Chat Completions.",
                error_code="PROVIDER_RESPONSES_UNSUPPORTED", safe_to_retry=False, affects_health=False, status_code=exc.code)
        return self.error_from_http_error(exc)

    def chat_completions_url(self, base_url: str) -> str:
        url = base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"
        return url

    def models_url(self, base_url: str) -> str:
        url = base_url.rstrip("/")
        # Sources also accept the full chat endpoint. Its catalog is a sibling,
        # not /chat/completions/models; probing the latter falsely degrades a
        # working Source and causes an unnecessary billable chat fallback.
        if url.endswith("/chat/completions"):
            url = url[: -len("/chat/completions")]
        if not url.endswith("/models"):
            url = f"{url}/models"
        return url

    def chat_payload(self, *, deployment, payload: dict[str, Any]) -> dict[str, Any]:
        if "_nexus_responses" in payload:
            from .responses_native import upstream_payload
            return upstream_payload(payload, deployment.model)
        upstream_payload = dict(payload)
        upstream_payload["model"] = deployment.model
        upstream_payload.pop("router_id", None)
        if "max_completion_tokens" in upstream_payload:
            upstream_payload.pop("max_tokens", None)
        return upstream_payload

    def headers(self, *, account, stream: bool = False) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream" if stream else "application/json"}
        key = decrypt_secret(account.encrypted_key)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _request_models(self, *, deployment, account, base_url: str) -> None:
        try:
            with open_provider_url(
                self.models_url(base_url),
                method="GET",
                headers=self.headers(account=account),
                timeout=float(getattr(settings, "NEXUS_DEPLOYMENT_HEALTH_TIMEOUT", 5)),
                allow_private=managed_provider_endpoint(deployment=deployment, url=base_url),
                allow_http=managed_provider_endpoint(deployment=deployment, url=base_url),
            ) as response:
                with _buffer_response(response):
                    pass
        except HTTPError as exc:
            raise self.error_from_http_error(exc) from exc
        except ProviderEndpointRejected as exc:
            raise endpoint_rejected_error(exc) from exc
        except (URLError, TimeoutError) as exc:
            raise ProviderClientError("Provider connection failed or timed out.", error_code="PROVIDER_TIMEOUT") from exc
        except ProviderClientError:
            raise
        except Exception as exc:  # noqa: BLE001 - converted to health result.
            raise ProviderClientError("Provider health response is invalid.") from exc

    def _request_minimal_chat(self, *, deployment, base_url: str) -> None:
        payload = {
            "model": deployment.model,
            "messages": [{"role": "user", "content": "health"}],
            "max_tokens": 1,
        }
        self.chat_completions(deployment=deployment, payload=payload)

    def _mock_response(self, *, deployment, payload: dict[str, Any], started: float) -> ProviderResponse:
        prompt_tokens = estimate_prompt_tokens(payload.get("messages", []))
        completion_tokens = 8
        raw = {
            "id": f"chatcmpl_{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": deployment.model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "mock gateway response",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
        if "_nexus_responses" in payload:
            from .openai_compat import chat_completion_to_response
            raw = chat_completion_to_response(raw)
        return build_response(raw=raw, model=deployment.model, started=started)

    def _mock_stream(self, *, deployment, payload: dict[str, Any]) -> Iterator[ProviderStreamEvent]:
        if "_nexus_responses" in payload:
            raw = self._mock_response(deployment=deployment, payload=payload, started=time.monotonic()).raw
            yield ProviderStreamEvent(raw={"type": "response.output_text.delta", "delta": "mock gateway response"}, text_delta="mock gateway response")
            yield ProviderStreamEvent(raw={"type": "response.completed", "response": raw}, usage=raw["usage"], finish_reason="stop")
            yield ProviderStreamEvent(done=True)
            return
        prompt_tokens = estimate_prompt_tokens(payload.get("messages", []))
        created = int(time.time())
        stream_id = f"chatcmpl_{uuid.uuid4().hex}"
        for index, text in enumerate(("mock ", "gateway ", "response")):
            chunk = {
                "id": stream_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": deployment.model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": text} if index else {"role": "assistant", "content": text},
                        "finish_reason": None,
                    }
                ],
                "usage": None,
            }
            yield ProviderStreamEvent(raw=chunk, text_delta=text)
        finish = {
            "id": stream_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": deployment.model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": None,
        }
        yield ProviderStreamEvent(raw=finish, finish_reason="stop")
        usage = {"prompt_tokens": prompt_tokens, "completion_tokens": 8, "total_tokens": prompt_tokens + 8}
        yield ProviderStreamEvent(
            raw={
                "id": stream_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": deployment.model,
                "choices": [],
                "usage": usage,
            },
            usage=usage,
        )
        yield ProviderStreamEvent(done=True)

    def error_from_http_error(self, exc: HTTPError) -> ProviderClientError:
        body = ""
        try:
            body = exc.read(65537)[:65536].decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - best-effort upstream error extraction.
            body = ""
        message = provider_error_message(status_code=exc.code, body=body)
        code = error_code_for_response(status_code=exc.code, body=body)
        rejected_input = exc.code in {400, 413, 422}
        if rejected_input:
            code = "PROVIDER_REQUEST_REJECTED"
        return ProviderClientError(
            message,
            status_code=exc.code,
            error_code=code,
            retry_after_seconds=retry_after_seconds(exc),
            safe_to_retry=False if rejected_input else None,
            affects_health=not (rejected_input or exc.code == 429 or code == "PROVIDER_QUOTA_EXHAUSTED"),
        )


class OpenAIProviderAdapter(OpenAICompatibleAdapter):
    pass


class QwenProviderAdapter(OpenAICompatibleAdapter):
    pass


class DeepSeekProviderAdapter(OpenAICompatibleAdapter):
    pass


class SystemOneAdapter(OpenAICompatibleAdapter):
    def systemone(self, *, deployment, payload, operation_id):
        from .system_one_protocol import strict_json, validate_response, MAX_BYTES
        started = time.monotonic()
        # Only the server-resolved Source determines credentials and model.
        native = {key: payload[key] for key in ("state", "questions")}
        native["model"] = deployment.upstream_model_id or deployment.model
        body = json.dumps(native, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(body) > MAX_BYTES:
            raise ProviderClientError("Native input exceeds 64 KiB.", affects_health=False, safe_to_retry=False)
        base = (deployment.endpoint or deployment.provider_account.url).rstrip("/")
        if base.endswith("/systemone"):
            base = base[:-len("/systemone")]
        try:
            with open_provider_url(base + "/systemone", method="POST", body=body,
                    headers={"Authorization": "Bearer " + decrypt_secret(deployment.provider_account.encrypted_key),
                        "Content-Type": "application/json", "Accept": "application/json", "Idempotency-Key": operation_id},
                    timeout=min(60, max(1, int(getattr(settings, "NEXUS_PROVIDER_REQUEST_TIMEOUT_SECONDS", 30))))) as response:
                encoded = response.read(MAX_BYTES + 1)
                if len(encoded) > MAX_BYTES:
                    raise ValueError("Oversized response")
                raw = strict_json(encoded)
            input_tokens, output_tokens = validate_response(raw, native["questions"])
            # Missing native token counts stay absent/null in the returned body.
            # The existing integer counters accumulate reported tokens only.
            return ProviderResponse(raw=raw, request_tokens=input_tokens or 0, response_tokens=output_tokens or 0,
                total_tokens=(input_tokens or 0) + (output_tokens or 0), model=raw["model"],
                latency_ms=elapsed_ms(started), operation="systemone")
        except HTTPError as exc:
            code = exc.code
            exc.close()
            raise ProviderClientError("System One upstream rejected the request; it was not replayed.", status_code=code,
                error_code="PROVIDER_RATE_LIMITED" if code == 429 else "SYSTEM_ONE_REQUEST_REJECTED",
                safe_to_retry=False, affects_health=False) from None
        except ProviderEndpointRejected as exc:
            raise endpoint_rejected_error(exc) from None
        except (URLError, TimeoutError, OSError) as exc:
            raise transport_client_error(exc) from None
        except (ValueError, TypeError, RecursionError):
            raise ProviderClientError("System One returned an invalid result; it was not replayed.",
                error_code="SYSTEM_ONE_RESULT_INVALID", affects_health=False, safe_to_retry=False) from None

    def chat_completions(self, **kwargs):
        raise ProviderClientError("System One is a decision service, not a Chat API.", error_code="MODEL_OPERATION_UNSUPPORTED", affects_health=False)

    def stream_chat_completions(self, **kwargs):
        raise ProviderClientError("System One decisions are non-streaming.", error_code="MODEL_OPERATION_UNSUPPORTED", affects_health=False)

    def decide(self, **kwargs):
        from .system_one import decide
        return decide(**kwargs)


def managed_provider_endpoint(*, deployment, url: str) -> bool:
    runtime = getattr(deployment, "provider_runtime", None)
    if runtime is None or getattr(runtime, "runtime_type", "") not in {"codex_proxy", "cliproxyapi"}:
        return False
    runtime_url = str(getattr(runtime, "internal_api_url", "") or "").rstrip("/")
    requested = str(url or "").rstrip("/")
    return bool(runtime_url and (requested == runtime_url or requested.startswith(runtime_url + "/")))


def endpoint_rejected_error(exc: ProviderEndpointRejected) -> ProviderClientError:
    # Transport validation messages contain only static reasons, not the URL,
    # credentials or provider response. No network request has been dispatched.
    return ProviderClientError(
        f"Provider endpoint validation failed before sending the request: {exc}",
        error_code="PROVIDER_ENDPOINT_REJECTED",
    )


def verify_openai_compatible_credentials(*, base_url: str, api_key: str, provider_name: str) -> None:
    """Verify candidate Direct API credentials before replacing a working secret.

    This deliberately performs a read-only models request and uses the same
    redirect-free, DNS-pinned transport as production inference.
    """
    adapter = adapter_for_provider(provider_name)
    parsed = urlparse(base_url)
    if parsed.hostname in settings.NEXUS_GATEWAY_MOCK_HOSTS:
        return
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        with open_provider_url(
            adapter.models_url(base_url),
            method="GET",
            headers=headers,
            timeout=float(getattr(settings, "NEXUS_DEPLOYMENT_HEALTH_TIMEOUT", 5)),
        ) as response:
            response.read(1)
    except HTTPError as exc:
        raise adapter.error_from_http_error(exc) from exc
    except ProviderEndpointRejected as exc:
        raise endpoint_rejected_error(exc) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ProviderClientError(
            "Provider credential verification could not reach the configured endpoint.",
            error_code="PROVIDER_CREDENTIAL_VERIFICATION_FAILED",
        ) from exc


def adapter_for_provider(provider_name: str) -> BaseProviderAdapter:
    adapters: dict[str, type[BaseProviderAdapter]] = {
        "openai": OpenAIProviderAdapter,
        "qwen": QwenProviderAdapter,
        "deepseek": DeepSeekProviderAdapter,
        "system_one": SystemOneAdapter,
        "system-one": SystemOneAdapter,
    }
    return adapters.get(provider_name, OpenAICompatibleAdapter)()


def error_code_for_status(status_code: int | None) -> str:
    if status_code in {401, 403}:
        return "PROVIDER_AUTH_FAILED"
    if status_code == 429:
        return "PROVIDER_RATE_LIMITED"
    if status_code and status_code >= 500:
        return "PROVIDER_UPSTREAM_UNAVAILABLE"
    return "PROVIDER_REQUEST_FAILED"


def error_code_for_response(*, status_code: int | None, body: str) -> str:
    lowered = str(body or "").lower()
    if status_code in {402, 429} and any(
        marker in lowered
        for marker in (
            "insufficient_quota",
            "quota exceeded",
            "quota_exceeded",
            "billing hard limit",
            "credit balance",
            "credits exhausted",
            "limit reached",
        )
    ):
        return "PROVIDER_QUOTA_EXHAUSTED"
    return error_code_for_status(status_code)


def retry_after_seconds(exc: HTTPError) -> int | None:
    value = exc.headers.get("Retry-After") if exc.headers else None
    if not value:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def provider_error_message(*, status_code: int, body: str) -> str:
    # Upstreams can echo credentials/prompts with arbitrary names and formats.
    # Keep classification (computed separately from bounded body), not raw text.
    return f"Provider returned HTTP {status_code}."


def build_response(*, raw: dict[str, Any], model: str, started: float) -> ProviderResponse:
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    request_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    response_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or request_tokens + response_tokens)
    input_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    output_details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
    cached_input_tokens = min(max(int(input_details.get("cached_tokens") or 0), 0), request_tokens)
    reasoning_output_tokens = min(max(int(output_details.get("reasoning_tokens") or 0), 0), response_tokens)
    latency_ms = elapsed_ms(started)
    return ProviderResponse(
        raw=raw,
        request_tokens=request_tokens,
        response_tokens=response_tokens,
        total_tokens=total_tokens,
        model=str(raw.get("model") or model),
        latency_ms=latency_ms,
        cached_input_tokens=cached_input_tokens,
        reasoning_output_tokens=reasoning_output_tokens,
    )


def iter_responses_sse(response) -> Iterator[ProviderStreamEvent]:
    events = iter_openai_sse(response)
    try:
        for event in events:
            if event.done:
                break  # [DONE] without response.completed is not success.
            raw = event.raw or {}
            kind = raw.get("type")
            if not isinstance(kind, str) or not kind.startswith("response.") and kind != "error":
                raise ProviderClientError("Source returned a non-Responses event.", error_code="PROVIDER_RESPONSES_INVALID", safe_to_retry=False, affects_health=False)
            if kind in {"response.failed", "error"}:
                raise ProviderClientError("Provider reported a Responses failure; no request was replayed.", error_code="PROVIDER_STREAM_FAILED", safe_to_retry=False, affects_health=False)
            if kind in {"response.completed", "response.incomplete"}:
                from .responses_native import validate_response
                result = validate_response(raw.get("response"))
                if kind != "response." + result["status"]:
                    raise ProviderClientError("Responses terminal event has an inconsistent status.", error_code="PROVIDER_RESPONSES_INVALID", safe_to_retry=False, affects_health=False)
                yield ProviderStreamEvent(raw=raw, usage=result.get("usage"), finish_reason="stop" if result["status"] == "completed" else "length")
                yield ProviderStreamEvent(done=True)
                return
            yield ProviderStreamEvent(raw=raw, text_delta=raw.get("delta", "") if kind == "response.output_text.delta" else "")
        raise ProviderClientError("Responses stream ended without a terminal response event.", error_code="PROVIDER_STREAM_INCOMPLETE", safe_to_retry=False)
    finally:
        events.close()


def iter_openai_sse(response) -> Iterator[ProviderStreamEvent]:
    limit = _response_limit("NEXUS_PROVIDER_SSE_MAX_LINE_BYTES", 64 * 1024 * 1024)
    event_limit = _response_limit("NEXUS_PROVIDER_SSE_MAX_EVENT_BYTES", limit)
    events = iter_events(response, limit=min(limit, event_limit), event_limit=event_limit)
    try:
        for event in events:
            if not event.data:
                continue
            if event.data.strip() == "[DONE]":
                yield ProviderStreamEvent(done=True)
                return
            payload = event.json()
            if not isinstance(payload, dict):
                raise ProviderClientError("Provider returned invalid streaming payload.", safe_to_retry=False)
            yield stream_event_from_openai_chunk(payload)
    except SSESizeError as exc:
        raise ProviderClientError(
            f"Provider streaming {exc.kind} exceeds the configured hard limit of {exc.limit} bytes "
            "(NEXUS_PROVIDER_SSE_MAX_LINE_BYTES / NEXUS_PROVIDER_SSE_MAX_EVENT_BYTES). "
            "The request was not automatically replayed.",
            error_code="PROVIDER_RESPONSE_TOO_LARGE", safe_to_retry=False, affects_health=False,
            error_details={"origin": "nexus", "stage": "upstream_decode", "unit": exc.kind,
                           "encoded_bytes": exc.size, "limit_bytes": exc.limit, "retry_safe": False},
        ) from exc
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ProviderClientError("Provider returned invalid streaming JSON.", safe_to_retry=False) from exc
    finally:
        events.close()


def stream_event_from_openai_chunk(payload: dict[str, Any]) -> ProviderStreamEvent:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else None
    text_delta = ""
    finish_reason = ""
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        content = delta.get("content")
        if isinstance(content, str):
            text_delta = content
        reason = choice.get("finish_reason")
        if isinstance(reason, str):
            finish_reason = reason
    return ProviderStreamEvent(raw=payload, text_delta=text_delta, finish_reason=finish_reason, usage=usage)


def estimate_prompt_tokens(messages: list[dict[str, Any]]) -> int:
    text = " ".join(message_text(message) for message in messages if isinstance(message, dict))
    return max(len(text.split()), 1)


def message_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def redact_provider_error(message: str) -> str:
    text = str(message)
    for marker in ("sk-", "Bearer "):
        if marker in text:
            before, _sep, _after = text.partition(marker)
            return f"{before}{marker}[REDACTED]"
    return text


def elapsed_ms(started: float) -> int:
    return max(int((time.monotonic() - started) * 1000), 1)
