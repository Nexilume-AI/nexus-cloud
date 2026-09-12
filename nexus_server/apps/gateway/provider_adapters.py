from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from collections.abc import Iterator
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

from django.conf import settings

from apps.common.crypto import decrypt_secret
from .provider_http import ProviderEndpointRejected, open_provider_url


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
    ) -> None:
        super().__init__(redact_provider_error(message))
        self.status_code = status_code
        self.error_code = error_code
        self.retry_after_seconds = retry_after_seconds


def _response_limit(name: str, default: int) -> int:
    return min(64 * 1024 * 1024, max(1, int(getattr(settings, name, default))))


def _read_response(response) -> bytes:
    limit = _response_limit("NEXUS_PROVIDER_RESPONSE_MAX_BYTES", 16 * 1024 * 1024)
    value = response.read(limit + 1)
    if len(value) > limit:
        raise ProviderClientError("Provider response exceeded the safe size limit.",
                                  error_code="PROVIDER_RESPONSE_TOO_LARGE")
    return value


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
        except (ProviderEndpointRejected, URLError, TimeoutError, OSError) as exc:
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
                self.chat_completions_url(base_url),
                method="POST",
                body=json.dumps(self.chat_payload(deployment=deployment, payload=payload)).encode("utf-8"),
                headers=self.headers(account=account),
                timeout=float(getattr(settings, "NEXUS_PROVIDER_REQUEST_TIMEOUT_SECONDS", 60)),
                allow_private=managed_provider_endpoint(deployment=deployment, url=base_url),
            ) as response:
                raw = json.loads(_read_response(response).decode("utf-8"))
        except HTTPError as exc:
            raise self.error_from_http_error(exc) from exc
        except (ProviderEndpointRejected, URLError, TimeoutError) as exc:
            raise ProviderClientError("Provider connection failed or timed out.", error_code="PROVIDER_TIMEOUT") from exc
        except ProviderClientError:
            raise
        except Exception as exc:  # noqa: BLE001 - converted at gateway boundary.
            raise ProviderClientError("Provider returned an invalid response.") from exc
        return build_response(raw=raw, model=deployment.model, started=started)

    def stream_chat_completions(self, *, deployment, payload: dict[str, Any]) -> Iterator[ProviderStreamEvent]:
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
        stream_options = upstream_payload.get("stream_options")
        if not isinstance(stream_options, dict):
            stream_options = {}
        stream_options.setdefault("include_usage", True)
        upstream_payload["stream_options"] = stream_options
        try:
            with open_provider_url(
                self.chat_completions_url(base_url),
                method="POST",
                body=json.dumps(upstream_payload).encode("utf-8"),
                headers=self.headers(account=account, stream=True),
                timeout=float(getattr(settings, "NEXUS_PROVIDER_REQUEST_TIMEOUT_SECONDS", 60)),
                allow_private=managed_provider_endpoint(deployment=deployment, url=base_url),
            ) as response:
                for event in iter_openai_sse(response):
                    yield event
        except HTTPError as exc:
            raise self.error_from_http_error(exc) from exc
        except (ProviderEndpointRejected, URLError, TimeoutError) as exc:
            raise ProviderClientError("Provider connection failed or timed out.", error_code="PROVIDER_TIMEOUT") from exc
        except ProviderClientError:
            raise
        except Exception as exc:  # noqa: BLE001 - converted at gateway boundary.
            raise ProviderClientError("Provider returned an invalid streaming response.") from exc

    def chat_completions_url(self, base_url: str) -> str:
        url = base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"
        return url

    def models_url(self, base_url: str) -> str:
        url = base_url.rstrip("/")
        if not url.endswith("/models"):
            url = f"{url}/models"
        return url

    def chat_payload(self, *, deployment, payload: dict[str, Any]) -> dict[str, Any]:
        upstream_payload = dict(payload)
        upstream_payload["model"] = deployment.model
        upstream_payload.pop("router_id", None)
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
            ) as response:
                _read_response(response)
        except HTTPError as exc:
            raise self.error_from_http_error(exc) from exc
        except (ProviderEndpointRejected, URLError, TimeoutError) as exc:
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
        return build_response(raw=raw, model=deployment.model, started=started)

    def _mock_stream(self, *, deployment, payload: dict[str, Any]) -> Iterator[ProviderStreamEvent]:
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
        return ProviderClientError(
            message,
            status_code=exc.code,
            error_code=error_code_for_response(status_code=exc.code, body=body),
            retry_after_seconds=retry_after_seconds(exc),
        )


class OpenAIProviderAdapter(OpenAICompatibleAdapter):
    pass


class QwenProviderAdapter(OpenAICompatibleAdapter):
    pass


class DeepSeekProviderAdapter(OpenAICompatibleAdapter):
    pass


def managed_provider_endpoint(*, deployment, url: str) -> bool:
    runtime = getattr(deployment, "provider_runtime", None)
    if runtime is None or getattr(runtime, "runtime_type", "") == "direct_api":
        return False
    runtime_url = str(getattr(runtime, "internal_api_url", "") or "").rstrip("/")
    requested = str(url or "").rstrip("/")
    return bool(runtime_url and (requested == runtime_url or requested.startswith(runtime_url + "/")))


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
    except (ProviderEndpointRejected, URLError, TimeoutError, OSError) as exc:
        raise ProviderClientError(
            "Provider credential verification could not reach the configured endpoint.",
            error_code="PROVIDER_CREDENTIAL_VERIFICATION_FAILED",
        ) from exc


def adapter_for_provider(provider_name: str) -> BaseProviderAdapter:
    adapters: dict[str, type[BaseProviderAdapter]] = {
        "openai": OpenAIProviderAdapter,
        "qwen": QwenProviderAdapter,
        "deepseek": DeepSeekProviderAdapter,
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


def iter_openai_sse(response) -> Iterator[ProviderStreamEvent]:
    limit = _response_limit("NEXUS_PROVIDER_SSE_MAX_LINE_BYTES", 1024 * 1024)
    while raw_line := response.readline(limit + 1):
        if len(raw_line) > limit:
            raise ProviderClientError("Provider streaming event exceeded the safe size limit.",
                                      error_code="PROVIDER_RESPONSE_TOO_LARGE")
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        data = line.split("data:", 1)[1].strip()
        if data == "[DONE]":
            yield ProviderStreamEvent(done=True)
            return
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ProviderClientError("Provider returned invalid streaming JSON.") from exc
        if not isinstance(payload, dict):
            raise ProviderClientError("Provider returned invalid streaming payload.")
        yield stream_event_from_openai_chunk(payload)


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
