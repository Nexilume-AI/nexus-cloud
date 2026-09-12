from __future__ import annotations

import json
import secrets
from datetime import timedelta
from typing import Mapping

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.common.crypto import decrypt_secret, encrypt_secret
from apps.common.subjects import hash_token

from .models import (
    AgentDisplayRun,
    AgentRunContextGrant,
    AgentRuntimeDeployment,
)


_CONTEXT_HEADERS = {
    "x-nexus-agui-run-id": "X-Nexus-AGUI-Run-Id",
    "x-nexus-agui-events-url": "X-Nexus-AGUI-Events-Url",
    "x-nexus-agui-token": "X-Nexus-AGUI-Token",
    "x-nexus-computer-enabled": "X-Nexus-Computer-Enabled",
    "x-nexus-terminal-url": "X-Nexus-Terminal-Url",
    "x-nexus-workspace-url": "X-Nexus-Workspace-Url",
    "x-nexus-memory-url": "X-Nexus-Memory-Url",
    "x-nexus-workspace-token": "X-Nexus-Workspace-Token",
    "x-nexus-workspace-root": "X-Nexus-Workspace-Root",
    "x-nexus-output-root": "X-Nexus-Output-Root",
    "x-nexus-workspace-delegate-url": "X-Nexus-Workspace-Delegate-Url",
    "x-nexus-workspace-delegate-token": "X-Nexus-Workspace-Delegate-Token",
    "x-nexus-workspace-capabilities": "X-Nexus-Workspace-Capabilities",
    "x-nexus-browser-enabled": "X-Nexus-Browser-Enabled",
    "x-nexus-browser-delegate-url": "X-Nexus-Browser-Delegate-Url",
    "x-nexus-browser-delegate-token": "X-Nexus-Browser-Delegate-Token",
    "x-nexus-browser-computer-name": "X-Nexus-Browser-Computer-Name",
    "x-nexus-interaction-url": "X-Nexus-Interaction-Url",
    "x-nexus-interaction-token": "X-Nexus-Interaction-Token",
    "x-nexus-interaction-mode": "X-Nexus-Interaction-Mode",
    "x-nexus-context-url": "X-Nexus-Context-Url",
    "x-nexus-context-token": "X-Nexus-Context-Token",
    "x-nexus-run-turn": "X-Nexus-Run-Turn",
    "x-nexus-checkpoint-url": "X-Nexus-Checkpoint-Url",
    "x-nexus-recovery-url": "X-Nexus-Recovery-Url",
    "x-nexus-recovery-managed": "X-Nexus-Recovery-Managed",
    "x-nexus-recovery-attempt": "X-Nexus-Recovery-Attempt",
    "x-nexus-recovery-replay": "X-Nexus-Recovery-Replay",
    "x-nexus-recovery-last-committed": "X-Nexus-Recovery-Last-Committed",
    "x-nexus-display-asset-url": "X-Nexus-Display-Asset-Url",
    "x-nexus-mobile-enabled": "X-Nexus-Mobile-Enabled",
    "x-nexus-mobile-delegate-url": "X-Nexus-Mobile-Delegate-Url",
    "x-nexus-mobile-delegate-token": "X-Nexus-Mobile-Delegate-Token",
    "x-nexus-mobile-capabilities": "X-Nexus-Mobile-Capabilities",
    "x-nexus-billing-url": "X-Nexus-Billing-Url",
    "x-nexus-billing-token": "X-Nexus-Billing-Token",
    "x-nexus-billing-currency": "X-Nexus-Billing-Currency",
    "x-nexus-billing-max-cost": "X-Nexus-Billing-Max-Cost",
    "x-nexus-usage-url": "X-Nexus-Usage-Url",
    "x-nexus-execution-profile": "X-Nexus-Execution-Profile",
    "x-nexus-execution-model": "X-Nexus-Execution-Model",
    "x-nexus-reasoning-effort": "X-Nexus-Reasoning-Effort",
    "x-nexus-execution-context-window": "X-Nexus-Execution-Context-Window",
    "x-nexus-input-files": "X-Nexus-Input-Files",
}


def _context_values(headers: Mapping[str, str]) -> dict[str, str]:
    return {
        _CONTEXT_HEADERS[key.lower()]: str(value)
        for key, value in headers.items()
        if key.lower() in _CONTEXT_HEADERS and str(value)
    }


def prepare_openwrt_run_context_headers(
    *,
    deployment: AgentRuntimeDeployment,
    headers: Mapping[str, str],
) -> dict[str, str]:
    """Replace full Run secrets with a 60-second single-use exchange grant."""

    values = _context_values(headers)
    safe = {
        str(key): str(value)
        for key, value in headers.items()
        if key.lower() not in _CONTEXT_HEADERS
        and not key.lower().startswith("x-nexus-run-context-")
    }
    if not values:
        return safe
    registration = deployment.edge_registration
    if registration is None:
        return safe
    run_id = values.get("X-Nexus-AGUI-Run-Id", "")
    run = AgentDisplayRun.objects.filter(
        id=run_id,
        agent=deployment.agent,
        runtime=deployment,
    ).first()
    if run is None:
        return safe
    token = secrets.token_urlsafe(32)
    grant = AgentRunContextGrant.objects.create(
        run=run,
        agent=deployment.agent,
        edge_registration=registration,
        token_hash=hash_token(token),
        encrypted_context=encrypt_secret(
            json.dumps(values, separators=(",", ":"), ensure_ascii=False)
        ),
        expires_at=timezone.now() + timedelta(seconds=60),
    )
    public_base = str(
        getattr(settings, "NEXUS_AGENT_CONTEXT_EXCHANGE_BASE_URL", "")
        or getattr(settings, "NEXUS_PUBLIC_BASE_URL", "http://localhost:8000")
    ).rstrip("/")
    safe["X-Nexus-Run-Context-Url"] = (
        f"{public_base}/api/v1/internal/agent-run-contexts/{grant.id}/exchange/"
    )
    safe["X-Nexus-Run-Context-Token"] = token
    return safe


@transaction.atomic
def redeem_openwrt_run_context(*, grant_id, token: str) -> dict[str, str] | None:
    if not token:
        return None
    grant = (
        AgentRunContextGrant.objects.select_for_update()
        .select_related("run", "agent", "edge_registration")
        .filter(id=grant_id, token_hash=hash_token(token))
        .first()
    )
    now = timezone.now()
    if (
        grant is None
        or grant.redeemed_at is not None
        or grant.expires_at <= now
        or grant.run.agent_id != grant.agent_id
        or grant.run.runtime_id is None
        or grant.run.runtime.edge_registration_id != grant.edge_registration_id
    ):
        return None
    try:
        value = json.loads(decrypt_secret(grant.encrypted_context))
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    context = {
        str(key): str(item)
        for key, item in value.items()
        if key in _CONTEXT_HEADERS.values() and isinstance(item, str)
    }
    if context.get("X-Nexus-AGUI-Run-Id") != str(grant.run_id):
        return None
    grant.redeemed_at = now
    grant.save(update_fields=["redeemed_at"])
    return context
