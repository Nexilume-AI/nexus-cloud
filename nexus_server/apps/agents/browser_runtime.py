"""Run-scoped Computer Runtime Browser; no Cloud SSH or local browser fallback."""
from __future__ import annotations
import base64
import hashlib
import json
import secrets
import uuid
from typing import Any
from urllib.parse import urlsplit
from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone
from rest_framework import exceptions
from apps.common.subjects import hash_token
from apps.workspaces.execution import redact_workspace_output
from .context_extension import close_legacy_browser_session
from .models import AgentBrowserSession, AgentDisplayAsset, AgentDisplayRun, AgentDisplayEvent


class AttachedBrowserError(exceptions.APIException):
    status_code = 503
    default_detail = "Attached Computer browser is unavailable."
    default_code = "BROWSER_UNAVAILABLE"


class AttachedBrowserComputerRequired(AttachedBrowserError):
    status_code = 409
    default_detail = "Attach a Computer before using this browser Agent."
    default_code = "BROWSER_COMPUTER_REQUIRED"


class AttachedBrowserPermissionRequired(AttachedBrowserError):
    status_code = 403
    default_detail = "Approve browser.control for this Agent before continuing."
    default_code = "BROWSER_PERMISSION_REQUIRED"


class AttachedBrowserTunnelUnavailable(AttachedBrowserError):
    default_detail = "The Attached Computer does not allow the protected browser tunnel."
    default_code = "BROWSER_TUNNEL_UNAVAILABLE"


class AttachedBrowserSessionLost(AttachedBrowserError):
    default_detail = "The Attached Computer browser session was lost."
    default_code = "BROWSER_SESSION_LOST"


class AttachedBrowserUnavailable(AttachedBrowserError):
    default_detail = "Browser automation is unavailable on the Attached Computer."
    default_code = "BROWSER_UNAVAILABLE"


class AttachedBrowserStaleObservation(AttachedBrowserError):
    status_code = 409
    default_detail = "The Browser page changed. Observe it again before acting."
    default_code = "BROWSER_STALE_OBSERVATION"


class AttachedBrowserActionFailed(AttachedBrowserError):
    status_code = 400
    default_detail = "The Attached Computer browser action failed."
    default_code = "BROWSER_ACTION_FAILED"


def _safe_http_url(value: Any) -> str:
    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
        parsed.port
    except ValueError:
        parsed = None
    if (
        parsed is None
        or parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise AttachedBrowserActionFailed("Only http and https URLs are supported.")
    return raw


def _safe_display_url(value: Any) -> str:
    raw = str(value or "")
    try:
        parsed = urlsplit(raw)
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port is not None else ""
        return f"{parsed.scheme}://{host}{port}{parsed.path}"
    except (ValueError, TypeError):
        return ""


def _validated_viewport(value: Any) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return (1280, 720)
    width, height = int(value[0]), int(value[1])
    if not 320 <= width <= 1920 or not 240 <= height <= 1200:
        raise AttachedBrowserActionFailed("Browser viewport is outside the supported range.")
    return (width, height)


def get_browser_delegate_run(*, run_id: str, token: str) -> AgentDisplayRun:
    run = (
        AgentDisplayRun.objects.filter(
            id=run_id,
            run_kind=AgentDisplayRun.KIND_INVOCATION,
            status=AgentDisplayRun.STATUS_RUNNING,
        )
        .select_related("agent", "computer_binding__connection")
        .first()
    )
    if run is None or run.computer_binding_id is None:
        raise AttachedBrowserComputerRequired()
    if (
        not token
        or not run.browser_delegate_token_hash
        or run.browser_delegate_token_expires_at is None
        or run.browser_delegate_token_expires_at <= timezone.now()
        or not secrets.compare_digest(run.browser_delegate_token_hash, hash_token(token))
    ):
        raise AttachedBrowserPermissionRequired()
    if "browser.control" not in set(run.workspace_capabilities_snapshot or []):
        raise AttachedBrowserPermissionRequired()
    return run


def _runtime_browser_session(*, run: AgentDisplayRun, viewport: tuple[int, int]) -> AgentBrowserSession:
    from apps.workspaces.computer_runtime import ensure_runtime_connection

    connection = run.computer_binding.connection
    device = ensure_runtime_connection(connection, operation="browser.open")
    current = AgentBrowserSession.objects.filter(run=run).first()
    if current is not None:
        if current.status == AgentBrowserSession.STATUS_FAILED:
            raise AttachedBrowserSessionLost(current.last_error or None)
        return current
    active = AgentBrowserSession.objects.filter(
        connection=connection,
        status__in=[AgentBrowserSession.STATUS_STARTING, AgentBrowserSession.STATUS_ACTIVE],
    ).count()
    maximum = max(int(getattr(settings, "NEXUS_ATTACHED_BROWSER_MAX_PER_COMPUTER", 2)), 1)
    if active >= maximum:
        raise AttachedBrowserError("BROWSER_COMPUTER_BUSY: this Computer is already running the maximum browser sessions.")
    session = AgentBrowserSession.objects.create(
        run=run,
        connection=connection,
        platform=device.platform,
        status=AgentBrowserSession.STATUS_STARTING,
    )
    session.runtime_session_id = str(session.id)
    session.save(update_fields=["runtime_session_id"])
    return session


def _store_runtime_observation(*, run: AgentDisplayRun, session: AgentBrowserSession, result: dict[str, Any], action: str, viewport: tuple[int, int]) -> dict[str, Any]:
    maximum = max(int(getattr(settings, "NEXUS_AGENT_BROWSER_CAPTURE_MAX_IMAGE_BYTES", 900000)), 64000)
    upload_reference = result.get("image_upload") if isinstance(result.get("image_upload"), dict) else None
    if upload_reference is not None:
        from apps.workspaces.computer_runtime import consume_runtime_upload

        try:
            image, uploaded_type = consume_runtime_upload(
                reference=upload_reference,
                connection=run.computer_binding.connection,
                display_run_id=str(run.id),
                maximum_bytes=maximum,
            )
        except Exception as exc:
            raise AttachedBrowserActionFailed("Computer Runtime Browser screenshot upload is unavailable.") from exc
    else:
        try:
            image = base64.b64decode(str(result.get("image_base64") or ""), validate=True)
        except (ValueError, TypeError) as exc:
            raise AttachedBrowserActionFailed("Computer Runtime returned an invalid Browser screenshot.") from exc
        uploaded_type = ""
    if not image or len(image) > maximum:
        raise AttachedBrowserActionFailed("Computer Runtime Browser screenshot exceeded the allowed size.")
    content_type = str(uploaded_type or result.get("content_type") or "image/jpeg").lower()
    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise AttachedBrowserActionFailed("Computer Runtime Browser screenshot type is not supported.")
    if content_type == "image/jpeg" and not image.startswith(b"\xff\xd8\xff"):
        raise AttachedBrowserActionFailed("Computer Runtime Browser screenshot content did not match its type.")
    if content_type == "image/png" and not image.startswith(b"\x89PNG\r\n\x1a\n"):
        raise AttachedBrowserActionFailed("Computer Runtime Browser screenshot content did not match its type.")
    if content_type == "image/webp" and not (len(image) >= 12 and image.startswith(b"RIFF") and image[8:12] == b"WEBP"):
        raise AttachedBrowserActionFailed("Computer Runtime Browser screenshot content did not match its type.")
    dom = result.get("dom") if isinstance(result.get("dom"), dict) else {"nodes": [], "truncated": False}
    revision = int(result.get("revision") or session.observation_revision + 1)
    page_url = str(result.get("url") or "")
    page_title = str(result.get("title") or "")[:512]
    suffix = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[content_type]
    asset = AgentDisplayAsset(
        run=run,
        content_type=content_type,
        size_bytes=len(image),
        sha256=hashlib.sha256(image).hexdigest(),
        width=int((result.get("viewport") or viewport)[0]),
        height=int((result.get("viewport") or viewport)[1]),
    )
    asset.file.save(f"frame.{suffix}", ContentFile(image), save=True)
    asset_url = f"/api/v1/agent-runs/{run.id}/display-assets/{asset.id}/"
    observation_id = uuid.uuid4().hex
    from .runtime_services import append_display_event

    append_display_event(
        run=run,
        event_type=AgentDisplayEvent.TYPE_COMPUTER_FRAME,
        payload={
            "name": "nexus.computer.frame",
            "value": {
                "frame_id": str(asset.id),
                "screenshot_url": asset_url,
                "url": _safe_display_url(page_url)[:2048],
                "title": page_title,
                "text": f"Attached Computer Runtime browser observation {revision}",
                "width": asset.width,
                "height": asset.height,
                "observation_id": observation_id,
                "revision": revision,
                "action": str(action)[:64],
                "action_status": "succeeded",
                "dom_node_count": len(dom.get("nodes") or []),
                "source": "attached_computer_runtime",
                "computer_name": run.computer_binding.connection.name,
                "profile": "isolated",
            },
        },
        visibility="private",
    )
    session.status = AgentBrowserSession.STATUS_ACTIVE
    session.observation_revision = revision
    session.last_error = ""
    session.save(update_fields=["status", "observation_revision", "last_error", "last_used_at"])
    return {
        "observation_id": observation_id,
        "revision": revision,
        "url": page_url,
        "title": page_title,
        "viewport": [asset.width, asset.height],
        "image_base64": base64.b64encode(image).decode("ascii"),
        "content_type": content_type,
        "dom": dom,
        "html": "",
        "frame_published": True,
        "computer_name": run.computer_binding.connection.name,
    }


def browser_delegate_operation(*, run_id: str, token: str, data: dict[str, Any]) -> dict[str, Any]:
    from apps.workspaces.computer_runtime import execute_runtime_command

    run = get_browser_delegate_run(run_id=run_id, token=token)
    operation = str(data.get("operation") or "").strip().lower()
    if operation not in {"open", "observe", "action", "close"}:
        raise AttachedBrowserActionFailed("Unsupported browser operation.")
    viewport = _validated_viewport(data.get("viewport"))
    idempotency_key = str(data.get("idempotency_key") or "").strip()
    if len(idempotency_key) > 128:
        raise AttachedBrowserActionFailed("Invalid browser operation key.")
    session = _runtime_browser_session(run=run, viewport=viewport)
    payload = {
        "browser_session_id": session.runtime_session_id,
        "run_id": str(run.id),
        "viewport": list(viewport),
    }
    if operation == "open":
        payload["url"] = _safe_http_url(data.get("url"))
        payload["timeout"] = min(max(float(data.get("timeout") or 30), 1), 60)
    elif operation == "action":
        payload["action"] = data.get("action") if isinstance(data.get("action"), dict) else {}
    try:
        result = execute_runtime_command(
            connection=run.computer_binding.connection,
            operation=f"browser.{operation}",
            required_scope="browser.control",
            payload=payload,
            timeout_seconds=65,
            display_run_id=str(run.id),
            caller_subject_hash=run.caller_subject_hash,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        code = str(getattr(exc, "default_code", ""))
        message = str(getattr(exc, "detail", exc))
        if code == "BROWSER_SESSION_LOST":
            AgentBrowserSession.objects.filter(id=session.id).update(status=AgentBrowserSession.STATUS_FAILED, last_error=message[:512], closed_at=timezone.now())
            raise AttachedBrowserSessionLost() from None
        if code == "BROWSER_UNAVAILABLE":
            raise AttachedBrowserUnavailable() from None
        if code == "BROWSER_STALE_OBSERVATION":
            raise AttachedBrowserStaleObservation() from None
        if code in {
            "COMPUTER_RUNTIME_OFFLINE",
            "COMPUTER_RUNTIME_REVOKED",
            "COMPUTER_CAPABILITY_UNAVAILABLE",
        }:
            # Preserve the failed dependency, not a generic Browser wrapper.
            # Never forward the Runtime's raw exception/endpoint to the caller.
            error = AttachedBrowserError("The attached Computer is offline, revoked or missing the required capability.")
            error.default_code = code
            error.detail = exceptions.ErrorDetail(str(error.detail), code=code)
            raise error from None
        if operation in {"action"}:
            action = payload.get("action") or {}
            if str(action.get("kind") or "") in {"fill", "type", "select"}:
                raise AttachedBrowserActionFailed() from None
        raise AttachedBrowserActionFailed(redact_workspace_output(message)[:300]) from None
    if operation == "close":
        AgentBrowserSession.objects.filter(id=session.id).update(status=AgentBrowserSession.STATUS_CLOSED, closed_at=timezone.now())
        return {"closed": True}
    return _store_runtime_observation(run=run, session=session, result=result, action=operation, viewport=viewport)


def close_attached_browser_session(*, run_id: str) -> None:
    session = AgentBrowserSession.objects.filter(run_id=run_id).select_related("connection").first()
    if session is None:
        return
    if session.connection.connection_type != "runtime":
        close_legacy_browser_session(run_id=run_id)
        return
    if session.status not in {AgentBrowserSession.STATUS_STARTING, AgentBrowserSession.STATUS_ACTIVE}:
        return
    try:
        from apps.workspaces.computer_runtime import execute_runtime_command

        execute_runtime_command(
            connection=session.connection,
            operation="browser.close",
            required_scope="browser.control",
            payload={"browser_session_id": session.runtime_session_id, "run_id": str(run_id)},
            timeout_seconds=10,
            display_run_id=str(run_id),
        )
    except Exception:
        # Runtime also expires exact Run-scoped profiles locally.
        pass
    AgentBrowserSession.objects.filter(id=session.id).update(
        status=AgentBrowserSession.STATUS_CLOSED,
        closed_at=timezone.now(),
    )
