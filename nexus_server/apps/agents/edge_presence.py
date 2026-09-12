"""Operational Edge presence without enrollment, IAM or publication services."""
from __future__ import annotations
import json
import secrets
from pathlib import Path
from typing import Any
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from apps.common.models import SoftDeleteModel
from .models import EdgeNode, AgentRuntimeDeployment


def _presence_sweeper_status_file() -> Path | None:
    filename = str(getattr(settings, "NEXUS_EDGE_PRESENCE_STATUS_FILE", "") or "").strip()
    if filename:
        return Path(filename)
    relay_control = str(getattr(settings, "NEXUS_RELAY_CONTROL_FILE", "") or "").strip()
    return Path(relay_control).with_name("presence-sweeper.json") if relay_control else None


def _presence_sweeper_interval_seconds() -> int:
    schedule = (getattr(settings, "CELERY_BEAT_SCHEDULE", {}) or {}).get("expire-edge-router-presence", {})
    value = schedule.get("schedule", 60) if isinstance(schedule, dict) else 60
    if hasattr(value, "total_seconds"):
        value = value.total_seconds()
    try:
        return max(1, min(int(value), 3600))
    except (TypeError, ValueError):
        return 60


def record_presence_sweeper_heartbeat(*, now=None, expired_count: int = 0) -> None:
    """Publish a credential-free heartbeat shared by the sweeper and API process."""

    filename = _presence_sweeper_status_file()
    if filename is None:
        return
    current = now or timezone.now()
    document = {
        "version": 1,
        "last_sweep_at": current.isoformat(),
        "interval_seconds": _presence_sweeper_interval_seconds(),
        "expired_last_run": max(0, int(expired_count)),
    }
    temporary = filename.with_name(f".{filename.name}.{secrets.token_hex(6)}.tmp")
    try:
        filename.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
        temporary.replace(filename)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def presence_sweeper_status(*, now=None) -> dict[str, Any]:
    current = now or timezone.now()
    interval = _presence_sweeper_interval_seconds()
    schedule = (getattr(settings, "CELERY_BEAT_SCHEDULE", {}) or {}).get("expire-edge-router-presence")
    automatic = bool(schedule) or _presence_sweeper_status_file() is not None
    status_file = _presence_sweeper_status_file()
    document: dict[str, Any] = {}
    if status_file is not None:
        try:
            loaded = json.loads(status_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                document = loaded
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            document = {}

    last_sweep_at = parse_datetime(str(document.get("last_sweep_at") or ""))
    stale_after_seconds = max(interval * 3, 180)
    running = bool(last_sweep_at and (current - last_sweep_at).total_seconds() <= stale_after_seconds)
    if not automatic:
        reason = "configuration_incomplete"
    elif last_sweep_at is None:
        reason = "starting"
    elif not running:
        reason = "heartbeat_stale"
    else:
        reason = "ready"
    return {
        "automatic": automatic,
        "running": running,
        "reason": reason,
        "interval_seconds": interval,
        "last_sweep_at": last_sweep_at.isoformat() if last_sweep_at else None,
        "expired_last_run": max(0, int(document.get("expired_last_run") or 0)),
    }


def _fail_edge_node_runtimes(*, node: EdgeNode, reason: str) -> None:
    AgentRuntimeDeployment.objects.filter(
        edge_registration__node=node,
        status__in=[
            AgentRuntimeDeployment.STATUS_ACTIVE,
            AgentRuntimeDeployment.STATUS_DEPLOYING,
        ],
    ).update(
        status=AgentRuntimeDeployment.STATUS_FAILED,
        health_status=AgentRuntimeDeployment.HEALTH_UNHEALTHY,
        last_error=reason,
    )


@transaction.atomic
def expire_edge_node_presence(*, now=None, batch_size: int = 500) -> int:
    current = now or timezone.now()
    nodes = list(
        EdgeNode.objects.select_for_update()
        .filter(
            status=SoftDeleteModel.STATUS_ACTIVE,
            presence_protocol_version__gte=EdgeNode.PRESENCE_PROTOCOL_V1,
            presence_expires_at__lte=current,
            connection_status__in=[EdgeNode.CONNECTION_ONLINE, EdgeNode.CONNECTION_DEGRADED],
        )
        .order_by("presence_expires_at")[:batch_size]
    )
    expired = 0
    for node in nodes:
        if node.presence_expires_at is None or node.presence_expires_at > current:
            continue
        node.connection_status = EdgeNode.CONNECTION_OFFLINE
        node.connection_status_reason = EdgeNode.PRESENCE_REASON_EXPIRED
        node.save(update_fields=["connection_status", "connection_status_reason", "updated_at"])
        _fail_edge_node_runtimes(node=node, reason="OpenWrt Router presence expired.")
        expired += 1
    record_presence_sweeper_heartbeat(now=current, expired_count=expired)
    return expired


