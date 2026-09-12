from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from .provider_adapters import (
    ProviderClientError,
    ProviderHealthResult,
    ProviderResponse,
    ProviderStreamEvent,
    adapter_for_provider,
    estimate_prompt_tokens,
)


class OpenAICompatibleClient:
    def check_health(self, *, deployment) -> ProviderHealthResult:
        return adapter_for_provider(deployment.provider.name).check_health(deployment=deployment)

    def chat_completions(self, *, deployment, payload: dict[str, Any]) -> ProviderResponse:
        return adapter_for_provider(deployment.provider.name).chat_completions(deployment=deployment, payload=payload)

    def stream_chat_completions(self, *, deployment, payload: dict[str, Any]) -> Iterator[ProviderStreamEvent]:
        return adapter_for_provider(deployment.provider.name).stream_chat_completions(deployment=deployment, payload=payload)
