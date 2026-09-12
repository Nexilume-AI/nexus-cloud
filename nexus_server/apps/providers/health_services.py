"""Provider operational health recording, independent of Marketplace catalogs."""
from __future__ import annotations
from .models import ProviderRuntimeAccount, ProviderRuntimeHealthCheck


def record_runtime_health_check(*, runtime: ProviderRuntimeAccount, status: str, reason: str = "", latency_ms: int = 0) -> ProviderRuntimeHealthCheck:
    return ProviderRuntimeHealthCheck.objects.create(
        runtime_account=runtime,
        tenant=runtime.tenant,
        status=status,
        reason=(reason or "")[:1024],
        latency_ms=max(int(latency_ms or 0), 0),
    )
