from __future__ import annotations

import json
import math
import os
import socket
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import connection


RUNTIME_HEARTBEAT_CACHE_KEY = "runtime:beat-worker-heartbeat:v1"
AGENT_WORKER_HEARTBEAT_CACHE_KEY = "runtime:agent-execution-worker:v1"
AGENT_RECONCILER_HEARTBEAT_CACHE_KEY = "runtime:agent-reconciler:v1"
AGENT_BUILDER_HEARTBEAT_CACHE_KEY = "runtime:agent-python-builder:v1"


def record_agent_worker_heartbeat():
    if getattr(settings, "NEXUS_PRODUCTION", False) and settings.NEXUS_PROCESS_ROLE != "worker":
        raise RuntimeError("Only a worker may publish Agent execution health.")
    cache.set(AGENT_WORKER_HEARTBEAT_CACHE_KEY, time.time(), timeout=90)


def agent_worker_readiness():
    if not getattr(settings, "NEXUS_PRODUCTION", False):
        return {"ok": True, "required": False}
    try:
        timestamp = cache.get(AGENT_WORKER_HEARTBEAT_CACHE_KEY)
        if timestamp is None or time.time() - float(timestamp) > 45:
            return {"ok": False, "code": "AGENT_WORKER_HEARTBEAT_MISSING"}
        return {"ok": True}
    except Exception:
        return {"ok": False, "code": "AGENT_WORKER_HEARTBEAT_UNAVAILABLE"}


def record_agent_reconciler_heartbeat(*, ok: bool, error_code: str = "") -> dict[str, Any]:
    payload = {
        "recorded_at": time.time(),
        "ok": bool(ok),
        "error_code": str(error_code or "")[:64],
        "worker": socket.gethostname(),
    }
    stale = int(getattr(settings, "NEXUS_AGENT_RECONCILER_STALE_SECONDS", 90))
    cache.set(AGENT_RECONCILER_HEARTBEAT_CACHE_KEY, payload, timeout=max(stale * 2, 120))
    return payload


def agent_reconciler_readiness() -> dict[str, Any]:
    if not getattr(settings, "NEXUS_PRODUCTION", False):
        return {"ok": True, "required": False}
    try:
        payload = cache.get(AGENT_RECONCILER_HEARTBEAT_CACHE_KEY)
    except Exception:
        return {"ok": False, "code": "AGENT_RECONCILER_HEARTBEAT_UNAVAILABLE"}
    if not isinstance(payload, dict) or not payload.get("recorded_at"):
        return {"ok": False, "code": "AGENT_RECONCILER_HEARTBEAT_MISSING"}
    age = max(0.0, time.time() - float(payload["recorded_at"]))
    stale = int(getattr(settings, "NEXUS_AGENT_RECONCILER_STALE_SECONDS", 90))
    if age > stale:
        return {"ok": False, "code": "AGENT_RECONCILER_HEARTBEAT_STALE", "age_seconds": round(age, 3)}
    if not payload.get("ok"):
        return {
            "ok": False,
            "code": payload.get("error_code") or "AGENT_RECONCILER_FAILED",
            "age_seconds": round(age, 3),
        }
    return {"ok": True, "age_seconds": round(age, 3)}


def _local_builder_status_file() -> Path | None:
    # Do not replace the shared-cache contract of multi-host production deployments.
    if getattr(settings, "NEXUS_PRODUCTION", False):
        return None
    filename = str(getattr(settings, "NEXUS_AGENT_BUILDER_STATUS_FILE", "") or "")
    if not filename:
        return None
    path = Path(filename)
    generation = str(getattr(settings, "NEXUS_AGENT_BUILDER_STATUS_GENERATION", "") or "")
    if not path.is_absolute() or path.is_symlink() or not generation or len(generation) > 128:
        raise ValueError("Invalid local Builder heartbeat configuration.")
    return path


def record_agent_builder_heartbeat() -> dict[str, Any]:
    role = str(getattr(settings, "NEXUS_PROCESS_ROLE", "")).strip().lower()
    status_file = _local_builder_status_file()
    if (getattr(settings, "NEXUS_PRODUCTION", False) or status_file is not None) and role != "agent-builder":
        raise RuntimeError("Only the Agent Python Builder may publish build health.")
    payload = {"recorded_at": time.time(), "worker": socket.gethostname(), "role": role or "development"}
    if status_file is not None:
        payload.update(version=1, generation=settings.NEXUS_AGENT_BUILDER_STATUS_GENERATION)
        status_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = None
        try:
            # Exclusive, owner-readable temp file; readers only see complete JSON.
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=status_file.parent,
                                             prefix=".builder-heartbeat-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(payload, stream, separators=(",", ":"), allow_nan=False)
            os.replace(temporary, status_file)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return payload
    stale = int(getattr(settings, "NEXUS_AGENT_BUILDER_STALE_SECONDS", 30))
    cache.set(AGENT_BUILDER_HEARTBEAT_CACHE_KEY, payload, timeout=max(stale * 2, 60))
    return payload


def agent_builder_readiness() -> dict[str, Any]:
    if not getattr(settings, "NEXUS_AGENT_PYTHON_BUILDS_ENABLED", False):
        return {"ok": True, "required": False}
    try:
        status_file = _local_builder_status_file()
        if status_file is None:
            payload = cache.get(AGENT_BUILDER_HEARTBEAT_CACHE_KEY)
        else:
            try:
                with status_file.open("rb") as stream:
                    raw = stream.read(4097)
            except FileNotFoundError:
                return {"ok": False, "code": "AGENT_BUILDER_HEARTBEAT_MISSING"}
            if len(raw) > 4096:
                raise ValueError("Oversized heartbeat.")
            payload = json.loads(raw)
            if not isinstance(payload, dict) or payload.get("version") != 1 or payload.get("role") != "agent-builder":
                raise ValueError("Invalid heartbeat.")
            if payload.get("generation") != settings.NEXUS_AGENT_BUILDER_STATUS_GENERATION:
                return {"ok": False, "code": "AGENT_BUILDER_HEARTBEAT_MISSING"}
        if not isinstance(payload, dict) or not payload.get("recorded_at"):
            return {"ok": False, "code": "AGENT_BUILDER_HEARTBEAT_MISSING"}
        timestamp = payload["recorded_at"]
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or timestamp <= 0:
            raise ValueError("Invalid heartbeat timestamp.")
        elapsed = time.time() - timestamp
        if elapsed < -5:
            raise ValueError("Future heartbeat timestamp.")
    except Exception:
        return {"ok": False, "code": "AGENT_BUILDER_HEARTBEAT_UNAVAILABLE"}
    age = max(0.0, elapsed)
    stale = int(getattr(settings, "NEXUS_AGENT_BUILDER_STALE_SECONDS", 30))
    if age > stale:
        return {"ok": False, "code": "AGENT_BUILDER_HEARTBEAT_STALE", "age_seconds": round(age, 3)}
    return {"ok": True, "age_seconds": round(age, 3)}


def record_runtime_heartbeat() -> dict[str, Any]:
    """Record proof that Beat dispatched a task and a dedicated Worker consumed it."""

    role = str(getattr(settings, "NEXUS_PROCESS_ROLE", "")).strip().lower()
    if getattr(settings, "NEXUS_PRODUCTION", False) and role != "worker":
        raise RuntimeError("Production runtime heartbeats may only be written by a worker process.")
    now = time.time()
    payload = {
        "recorded_at": now,
        "worker": socket.gethostname(),
        "role": role or "development",
    }
    stale_seconds = int(getattr(settings, "NEXUS_RUNTIME_HEARTBEAT_STALE_SECONDS", 45))
    cache.set(RUNTIME_HEARTBEAT_CACHE_KEY, payload, timeout=max(stale_seconds * 2, 60))
    return payload


def runtime_readiness() -> tuple[bool, dict[str, Any]]:
    from apps.providers.provider_controller import provider_runtime_controller_readiness
    from apps.agents.runtime_controller import agent_runtime_controller_readiness

    checks = {
        "database": _database_check(),
        "redis": _redis_check(),
        "background_tasks": _background_task_check(),
        "agent_execution": agent_worker_readiness(),
        "agent_reconciliation": agent_reconciler_readiness(),
        "agent_hosting": agent_runtime_controller_readiness(),
        "agent_builds": agent_builder_readiness(),
        "process": _process_check(),
        "shared_storage": _shared_storage_check(),
        "provider_execution": provider_runtime_controller_readiness(),
    }
    ready = all(check["ok"] for check in checks.values())
    return ready, {"status": "ready" if ready else "not_ready", "checks": checks}


def _database_check() -> dict[str, Any]:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        vendor = connection.vendor
        if getattr(settings, "NEXUS_PRODUCTION", False) and vendor != "postgresql":
            return {"ok": False, "code": "POSTGRESQL_REQUIRED", "vendor": vendor}
        return {"ok": True, "vendor": vendor}
    except Exception as exc:  # pragma: no cover - exercised by integration probes
        return {"ok": False, "code": "DATABASE_UNAVAILABLE", "error_type": type(exc).__name__}


def _redis_check() -> dict[str, Any]:
    key = f"runtime:readiness:{uuid.uuid4().hex}"
    try:
        cache.set(key, "ok", timeout=10)
        value = cache.get(key)
        cache.delete(key)
        if value != "ok":
            return {"ok": False, "code": "CACHE_ROUND_TRIP_FAILED"}
        backend = str(settings.CACHES["default"].get("BACKEND", ""))
        if getattr(settings, "NEXUS_PRODUCTION", False) and "redis" not in backend.lower():
            return {"ok": False, "code": "REDIS_REQUIRED"}
        return {"ok": True, "backend": backend.rsplit(".", 1)[-1]}
    except Exception as exc:  # pragma: no cover - exercised by integration probes
        return {"ok": False, "code": "REDIS_UNAVAILABLE", "error_type": type(exc).__name__}


def _background_task_check() -> dict[str, Any]:
    if not getattr(settings, "NEXUS_PRODUCTION", False):
        return {"ok": True, "required": False}
    try:
        heartbeat = cache.get(RUNTIME_HEARTBEAT_CACHE_KEY)
    except Exception as exc:  # pragma: no cover - covered by the Redis check
        return {"ok": False, "code": "HEARTBEAT_UNAVAILABLE", "error_type": type(exc).__name__}
    if not isinstance(heartbeat, dict) or not heartbeat.get("recorded_at"):
        return {"ok": False, "code": "WORKER_BEAT_HEARTBEAT_MISSING"}
    age_seconds = max(0.0, time.time() - float(heartbeat["recorded_at"]))
    stale_seconds = int(getattr(settings, "NEXUS_RUNTIME_HEARTBEAT_STALE_SECONDS", 45))
    if age_seconds > stale_seconds:
        return {
            "ok": False,
            "code": "WORKER_BEAT_HEARTBEAT_STALE",
            "age_seconds": round(age_seconds, 3),
        }
    if heartbeat.get("role") != "worker":
        return {"ok": False, "code": "HEARTBEAT_NOT_FROM_WORKER"}
    return {"ok": True, "age_seconds": round(age_seconds, 3)}


def _process_check() -> dict[str, Any]:
    if not getattr(settings, "NEXUS_PRODUCTION", False):
        return {"ok": True, "role": "development"}
    role = str(getattr(settings, "NEXUS_PROCESS_ROLE", ""))
    if role != "web":
        return {"ok": False, "code": "WEB_PROCESS_ROLE_REQUIRED", "role": role}
    return {"ok": True, "role": role}


def _shared_storage_check() -> dict[str, Any]:
    root_value = str(getattr(settings, "NEXUS_SHARED_STORAGE_ROOT", "")).strip()
    configured = bool(root_value)
    if getattr(settings, "NEXUS_PRODUCTION", False) and not configured:
        return {"ok": False, "code": "SHARED_STORAGE_REQUIRED"}
    if not configured:
        return {"ok": True, "configured": False}
    probe = Path(root_value) / f".nexus-readiness-{uuid.uuid4().hex}"
    try:
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        if probe.read_text(encoding="utf-8") != "ok":
            return {"ok": False, "code": "SHARED_STORAGE_ROUND_TRIP_FAILED"}
        return {"ok": True, "configured": True, "writable": True}
    except OSError as exc:  # pragma: no cover - exercised by integration probes
        return {"ok": False, "code": "SHARED_STORAGE_UNAVAILABLE", "error_type": type(exc).__name__}
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
