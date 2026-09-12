from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from http.cookies import SimpleCookie
from importlib import import_module
from urllib.parse import parse_qs
from urllib.parse import urlsplit

from apps.common.async_database import database_sync_to_async as sync_to_async
from django.conf import settings
from django.contrib.auth import get_user
from django.contrib.auth import get_user_model
from django.http import HttpRequest
from django.core.cache import cache
from django.db import transaction

from apps.common.jwt import decode_jwt
from apps.common.subjects import hash_token
from apps.common.subjects import subject_digest
from apps.common.authorization import has_nexus_permission
from .context_policy import terminal_context_valid, validate_terminal_identity


TERMINAL_WS_PATH = re.compile(r"^/ws/workspace-terminals/(?P<session_id>[0-9a-fA-F-]{36})/$")
AGENT_RUN_TERMINAL_WS_PATH = re.compile(r"^/ws/agent-runs/(?P<run_id>[0-9a-fA-F-]{36})/terminal/$")
COMPUTER_RUNTIME_WS_PATH = re.compile(r"^/ws/computer-runtime/v1/connect/$")
logger = logging.getLogger(__name__)


class WorkspaceASGIProxy:
    def __init__(self, django_app):
        self.django_app = django_app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket" and COMPUTER_RUNTIME_WS_PATH.match(scope.get("path", "")):
            from .computer_runtime_asgi import computer_runtime_websocket

            await computer_runtime_websocket(scope=scope, receive=receive, send=send)
            return
        if scope["type"] == "websocket" and TERMINAL_WS_PATH.match(scope.get("path", "")):
            await workspace_terminal(scope=scope, receive=receive, send=send)
            return
        if scope["type"] == "websocket" and AGENT_RUN_TERMINAL_WS_PATH.match(scope.get("path", "")):
            await agent_run_terminal(scope=scope, receive=receive, send=send)
            return
        await self.django_app(scope, receive, send)


async def workspace_terminal(*, scope, receive, send) -> None:
    channel = None
    try:
        session = await resolve_terminal_session(scope)
    except Exception as exc:
        logger.warning("Workspace terminal authorization failed: %s", type(exc).__name__)
        await send({"type": "websocket.close", "code": 4401})
        return
    try:
        # Opening a Computer Runtime terminal waits for a command result from
        # another WebSocket handled by this same ASGI process.  It must not
        # occupy asgiref's thread-sensitive executor, otherwise the Runtime
        # socket cannot claim the command and both sides deadlock until timeout.
        runtime_connection = session.connection.connection_type == "runtime"
        channel = await sync_to_async(
            open_terminal_for_session,
            thread_sensitive=not runtime_connection,
        )(session=session)
    except Exception as exc:
        # Device errors can contain command text, credentials or personal paths.
        # Keep operational correlation without copying exception contents to logs.
        logger.warning(
            "Workspace terminal open failed for session %s: %s",
            session.id,
            type(exc).__name__,
        )
        await send({"type": "websocket.close", "code": 1011})
        return

    accept_event = {"type": "websocket.accept"}
    if "nexus-terminal-v1" in scope.get("subprotocols", []):
        accept_event["subprotocol"] = "nexus-terminal-v1"
    await send(accept_event)
    await send_json(send, {"type": "status", "status": "connected"})

    try:
        reader = asyncio.create_task(pump_terminal_to_client(send=send, channel=channel))
        writer = asyncio.create_task(pump_client_to_terminal(receive=receive, channel=channel))
        done, pending = await asyncio.wait({reader, writer}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.result()
    except Exception as exc:
        await sync_to_async(mark_session_failed)(session_id=str(session.id), error=str(exc))
        await send({"type": "websocket.close", "code": 1011})
    finally:
        if channel is not None:
            await asyncio.to_thread(channel.close)


async def agent_run_terminal(*, scope, receive, send) -> None:
    try:
        session = await resolve_agent_run_terminal_ticket(scope)
    except Exception:
        await send({"type": "websocket.close", "code": 4404})
        return
    await send({"type": "websocket.accept"})
    await send_json(send, {"type": "status", "status": session.status, "viewer_mode": "read_only"})
    cursor = 0
    while True:
        rows = await terminal_transcript_after(session_id=str(session.id), cursor=cursor)
        for row in rows:
            cursor = row["seq"]
            await send_json(send, {"type": "terminal", **row})
        try:
            event = await asyncio.wait_for(receive(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        if event.get("type") == "websocket.disconnect":
            return
        # Agent terminal viewers are strictly read-only: input, resize and
        # control frames all terminate the socket.
        if event.get("type") == "websocket.receive":
            await send({"type": "websocket.close", "code": 4403})
            return


@sync_to_async
def resolve_agent_run_terminal_ticket(scope):
    from .models import WorkspaceTerminalSession

    match = AGENT_RUN_TERMINAL_WS_PATH.match(scope.get("path", ""))
    if not match:
        raise ValueError("Not an Agent run terminal path.")
    query = parse_qs(scope.get("query_string", b"").decode("utf-8"))
    ticket = str((query.get("ticket") or [""])[0])
    if not ticket:
        raise ValueError("Terminal ticket is required.")
    cache_key = "agent-terminal-ticket:" + hash_token(ticket)
    payload = cache.get(cache_key)
    cache.delete(cache_key)
    if not isinstance(payload, dict) or payload.get("run_id") != match.group("run_id"):
        raise ValueError("Terminal ticket is invalid.")
    return WorkspaceTerminalSession.objects.select_related("display_run").get(
        id=payload.get("session_id"),
        display_run_id=payload.get("run_id"),
        session_kind=WorkspaceTerminalSession.KIND_AGENT_RUN,
    )


@sync_to_async
def terminal_transcript_after(*, session_id: str, cursor: int) -> list[dict]:
    from .models import WorkspaceTerminalTranscript

    return [
        {
            "seq": row.seq,
            "kind": row.kind,
            "command_id": row.command_id,
            "data": row.data,
            "exit_code": row.exit_code,
            "created_at": row.created_at.isoformat(),
        }
        for row in WorkspaceTerminalTranscript.objects.filter(session_id=session_id, seq__gt=cursor).order_by("seq")[:500]
    ]


async def pump_terminal_to_client(*, send, channel) -> None:
    while True:
        data = await asyncio.to_thread(channel.read, 0.25)
        if data:
            await send_json(send, {"type": "output", "data": data})
        await asyncio.sleep(0.02)


async def pump_client_to_terminal(*, receive, channel) -> None:
    while True:
        event = await receive()
        event_type = event.get("type")
        if event_type == "websocket.disconnect":
            return
        if event_type != "websocket.receive":
            continue
        payload = parse_client_message(event)
        if payload.get("type") == "input":
            await asyncio.to_thread(channel.write, str(payload.get("data", "")))
        elif payload.get("type") == "resize":
            await asyncio.to_thread(channel.resize, cols=int(payload.get("cols") or 100), rows=int(payload.get("rows") or 30))


def parse_client_message(event: dict) -> dict:
    text = event.get("text")
    if text is None and event.get("bytes") is not None:
        text = event["bytes"].decode("utf-8", errors="replace")
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {"type": "input", "data": text}
    return payload if isinstance(payload, dict) else {}


async def send_json(send, payload: dict) -> None:
    await send({"type": "websocket.send", "text": json.dumps(payload, separators=(",", ":"))})


@sync_to_async
def resolve_terminal_session(scope):
    from .models import WorkspaceTerminalSession

    match = TERMINAL_WS_PATH.match(scope.get("path", ""))
    if not match:
        raise ValueError("Not a terminal websocket path.")
    session_id = match.group("session_id")
    query = parse_qs(scope.get("query_string", b"").decode("utf-8"))
    ticket = str((query.get("ticket") or [""])[0]) or terminal_ticket_from_subprotocols(scope)
    if ticket:
        now = int(time.time())
        ticket_hash = hash_token(ticket)
        with transaction.atomic():
            # Only the session owns the single-use ticket. Locking joined rows
            # also locks the nullable Project outer join, which PostgreSQL rejects.
            session = WorkspaceTerminalSession.objects.select_for_update(of=("self",)).select_related(
                "tenant", "connection", "project"
            ).get(id=session_id, session_kind=WorkspaceTerminalSession.KIND_USER)
            metadata = dict(session.metadata or {})
            valid_tickets = [
                item
                for item in metadata.get("_websocket_tickets", [])
                if isinstance(item, dict) and int(item.get("expires_at") or 0) > now
            ]
            matching_ticket = next((item for item in valid_tickets if item.get("hash") == ticket_hash), None)
            if matching_ticket is None:
                raise ValueError("Terminal ticket is invalid.")
            if session.connection.owner_subject_hash != matching_ticket.get("owner_subject_hash"):
                raise ValueError("Terminal session not found.")
            if not terminal_context_valid(session):
                raise ValueError("Terminal session not found.")
            metadata["_websocket_tickets"] = [item for item in valid_tickets if item is not matching_ticket]
            session.metadata = metadata
            session.save(update_fields=["metadata", "updated_at"])
        return session
    token = (query.get("token") or [""])[0]
    if not token:
        token = bearer_token_from_headers(scope)
    if token:
        payload = decode_jwt(token)
        user_id = payload.get("sub") or payload.get("user_id")
        if not user_id:
            raise ValueError("JWT subject is required.")
        user_model = get_user_model()
        user = user_model.objects.get(pk=user_id, is_active=True)
    else:
        validate_session_websocket_origin(scope)
        user = session_user_from_scope(scope)
    validate_terminal_identity(scope=scope, user=user, token=token)
    session = WorkspaceTerminalSession.objects.select_related("tenant", "connection", "project").get(id=session_id)
    token_tenant_id = payload.get("tenant_id") if token else ""
    tenant_id = (query.get("tenant_id") or [""])[0] or token_tenant_id or ""
    if tenant_id and str(session.tenant_id) != str(tenant_id):
        raise ValueError("Tenant mismatch.")
    if session.session_kind != WorkspaceTerminalSession.KIND_USER:
        raise ValueError("Agent run terminals require a one-time viewer ticket.")
    principal_type = "service_account" if getattr(user, "is_service_account_principal", False) else "user"
    principal_id = str(getattr(user, "pk", "") or getattr(user, "id", ""))
    subject_hash = subject_digest(f"{session.tenant_id}|{principal_type}|{principal_id}")
    if session.connection.owner_subject_hash != subject_hash:
        raise ValueError("Terminal session not found.")
    if not terminal_context_valid(session):
        raise ValueError("Terminal session not found.")
    return session


def terminal_ticket_from_subprotocols(scope) -> str:
    prefix = "nexus-terminal-ticket."
    for protocol in scope.get("subprotocols", []):
        value = str(protocol)
        if value.startswith(prefix):
            return value[len(prefix):]
    return ""


def bearer_token_from_headers(scope) -> str:
    for key, value in scope.get("headers", []):
        if key.lower() != b"authorization":
            continue
        text = value.decode("latin1")
        if text.lower().startswith("bearer "):
            return text[7:].strip()
    return ""


def session_user_from_scope(scope):
    session_key = session_key_from_scope(scope)
    if not session_key:
        raise ValueError("Authenticated web session is required.")
    session_engine = import_module(settings.SESSION_ENGINE)
    request = HttpRequest()
    request.session = session_engine.SessionStore(session_key=session_key)
    user = get_user(request)
    if not user.is_authenticated or not user.is_active:
        raise ValueError("Authenticated web session is required.")
    return user


def session_key_from_scope(scope) -> str:
    cookie = SimpleCookie()
    for key, value in scope.get("headers", []):
        if key.lower() == b"cookie":
            cookie.load(value.decode("latin1"))
    session_cookie = cookie.get(settings.SESSION_COOKIE_NAME)
    return session_cookie.value if session_cookie else ""


def validate_session_websocket_origin(scope) -> None:
    origin = header_value(scope, b"origin")
    host = header_value(scope, b"host")
    if not origin or not host:
        raise ValueError("WebSocket origin is required for session authentication.")
    parsed_origin = urlsplit(origin)
    if parsed_origin.scheme not in {"http", "https"} or not parsed_origin.netloc:
        raise ValueError("WebSocket origin is not allowed.")
    if parsed_origin.netloc.lower() == host.lower():
        return
    if any(origin_matches_trusted_origin(origin=parsed_origin, trusted=trusted) for trusted in settings.CSRF_TRUSTED_ORIGINS):
        return
    raise ValueError("WebSocket origin is not allowed.")


def header_value(scope, name: bytes) -> str:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value.decode("latin1").strip()
    return ""


def origin_matches_trusted_origin(*, origin, trusted: str) -> bool:
    parsed_trusted = urlsplit(str(trusted).rstrip("/"))
    if origin.scheme.lower() != parsed_trusted.scheme.lower():
        return False
    trusted_netloc = parsed_trusted.netloc.lower()
    origin_netloc = origin.netloc.lower()
    if trusted_netloc.startswith("*."):
        base_netloc = trusted_netloc[2:]
        return origin_netloc == base_netloc or origin_netloc.endswith(f".{base_netloc}")
    return origin_netloc == trusted_netloc


def open_terminal_for_session(*, session):
    from .execution import mark_terminal_session_active, open_persistent_terminal

    channel = open_persistent_terminal(session=session)
    mark_terminal_session_active(session=session)
    return channel


def mark_session_closed(*, session_id: str) -> None:
    from .models import WorkspaceTerminalSession
    from .execution import mark_terminal_session_closed

    session = WorkspaceTerminalSession.objects.get(id=session_id)
    mark_terminal_session_closed(session=session)


def mark_session_failed(*, session_id: str, error: str) -> None:
    from .models import WorkspaceTerminalSession
    from .execution import mark_terminal_session_failed

    session = WorkspaceTerminalSession.objects.get(id=session_id)
    mark_terminal_session_failed(session=session, error=error)
