from __future__ import annotations

import asyncio
import json
import queue
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from apps.common.async_database import database_sync_to_async as sync_to_async
from django.conf import settings

from .computer_runtime import (
    ComputerCapabilityUnavailable,
    ComputerRuntimeError,
    ComputerRuntimeOffline,
    apply_runtime_frame,
    claim_cancel_frames,
    claim_pending_commands,
    consume_connect_ticket,
    ensure_runtime_connection,
    runtime_generation_is_current,
)


MAX_TERMINAL_STREAM_FRAME_BYTES = 64 * 1024


@dataclass
class _TerminalStreamState:
    stream_id: str
    opened: threading.Event = field(default_factory=threading.Event)
    messages: queue.Queue[dict[str, Any]] = field(default_factory=lambda: queue.Queue(maxsize=256))
    error: str = ""
    closed: bool = False

    def deliver(self, frame: dict[str, Any]) -> None:
        frame_type = str(frame.get("type") or "")
        if frame_type == "terminal_stream_opened":
            self.opened.set()
            return
        if frame_type == "terminal_stream_output":
            data = str(frame.get("data") or "")
            if len(data.encode("utf-8", errors="replace")) > MAX_TERMINAL_STREAM_FRAME_BYTES:
                self.fail("Computer Runtime terminal output frame exceeded its limit.")
                return
            try:
                self.messages.put_nowait({"type": "output", "data": data})
            except queue.Full:
                self.fail("Computer Runtime terminal output could not keep up with the client.")
            return
        if frame_type == "terminal_stream_error":
            self.fail(str(frame.get("message") or "Computer Runtime terminal stream failed."))
            return
        if frame_type == "terminal_stream_closed":
            self.finish()

    def fail(self, message: str) -> None:
        self.error = str(message)[:500]
        self.closed = True
        self.opened.set()
        self._signal_closed()

    def finish(self) -> None:
        self.closed = True
        self.opened.set()
        self._signal_closed()

    def _signal_closed(self) -> None:
        try:
            self.messages.put_nowait({"type": "closed"})
        except queue.Full:
            try:
                self.messages.get_nowait()
            except queue.Empty:
                pass
            try:
                self.messages.put_nowait({"type": "closed"})
            except queue.Full:
                pass


class _RuntimeSocketState:
    def __init__(self, *, device_id: str, generation: int, loop: asyncio.AbstractEventLoop) -> None:
        self.device_id = device_id
        self.generation = generation
        self.loop = loop
        self.outgoing: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=512)
        self.streams: dict[str, _TerminalStreamState] = {}
        self.lock = threading.RLock()
        self.closed = False
        self.last_client_sequence = 0

    def create_stream(self) -> _TerminalStreamState:
        stream = _TerminalStreamState(stream_id=uuid.uuid4().hex)
        with self.lock:
            if self.closed:
                raise ComputerRuntimeOffline("Nexus Computer Runtime connection is unavailable.")
            self.streams[stream.stream_id] = stream
        return stream

    def send(self, frame: dict[str, Any], *, timeout: float = 2.0) -> None:
        with self.lock:
            if self.closed:
                raise ComputerRuntimeOffline("Nexus Computer Runtime connection is unavailable.")
        future = asyncio.run_coroutine_threadsafe(self.outgoing.put(frame), self.loop)
        try:
            future.result(timeout=timeout)
        except Exception as exc:
            future.cancel()
            raise ComputerRuntimeOffline("Nexus Computer Runtime terminal stream is unavailable.") from exc

    def deliver(self, frame: dict[str, Any]) -> None:
        stream_id = str(frame.get("stream_id") or "")
        with self.lock:
            stream = self.streams.get(stream_id)
        if stream is not None:
            stream.deliver(frame)

    def accept_sequence(self, frame: dict[str, Any]) -> bool:
        try:
            sequence = int(frame.get("sequence") or 0)
        except (TypeError, ValueError) as exc:
            raise ComputerRuntimeError("Computer Runtime frame sequence is invalid.") from exc
        if sequence <= 0:
            raise ComputerRuntimeError("Computer Runtime frame sequence is invalid.")
        with self.lock:
            if sequence <= self.last_client_sequence:
                return False
            self.last_client_sequence = sequence
        return True

    def remove_stream(self, stream_id: str) -> None:
        with self.lock:
            self.streams.pop(stream_id, None)

    def fail_all(self, message: str) -> None:
        with self.lock:
            self.closed = True
            streams = list(self.streams.values())
            self.streams.clear()
        for stream in streams:
            stream.fail(message)


class RuntimeTerminalStreamChannel:
    """Synchronous terminal facade backed by direct frames on the Runtime WSS."""

    def __init__(self, *, socket: _RuntimeSocketState, stream: _TerminalStreamState) -> None:
        self.socket = socket
        self.stream = stream
        self.closed = False

    def read(self, timeout: float = 0.25) -> str:
        if self.closed:
            return ""
        try:
            message = self.stream.messages.get(timeout=max(0.01, float(timeout)))
        except queue.Empty:
            return ""
        if message.get("type") == "output":
            return str(message.get("data") or "")
        self.closed = True
        if self.stream.error:
            raise ComputerRuntimeOffline(self.stream.error)
        return ""

    def write(self, data: str) -> None:
        if self.closed:
            raise ComputerRuntimeOffline("Computer Runtime terminal stream is closed.")
        value = str(data)
        if len(value.encode("utf-8", errors="replace")) > MAX_TERMINAL_STREAM_FRAME_BYTES:
            raise ComputerRuntimeError("Terminal input frame exceeded its limit.")
        self.socket.send({
            "version": 1,
            "type": "terminal_stream_input",
            "stream_id": self.stream.stream_id,
            "data": value,
        })

    def resize(self, *, cols: int, rows: int) -> None:
        if not self.closed:
            self.socket.send({
                "version": 1,
                "type": "terminal_stream_resize",
                "stream_id": self.stream.stream_id,
                "cols": max(1, min(int(cols), 1000)),
                "rows": max(1, min(int(rows), 1000)),
            })

    def close(self) -> None:
        if self.closed:
            self.socket.remove_stream(self.stream.stream_id)
            return
        self.closed = True
        try:
            self.socket.send({
                "version": 1,
                "type": "terminal_stream_close",
                "stream_id": self.stream.stream_id,
            })
        except ComputerRuntimeError:
            pass
        finally:
            self.socket.remove_stream(self.stream.stream_id)


_RUNTIME_SOCKETS: dict[str, _RuntimeSocketState] = {}
_RUNTIME_SOCKETS_LOCK = threading.RLock()


def _register_runtime_socket(*, device_id: str, generation: int) -> _RuntimeSocketState:
    state = _RuntimeSocketState(device_id=device_id, generation=generation, loop=asyncio.get_running_loop())
    with _RUNTIME_SOCKETS_LOCK:
        previous = _RUNTIME_SOCKETS.get(device_id)
        _RUNTIME_SOCKETS[device_id] = state
    if previous is not None:
        previous.fail_all("Computer Runtime connection was replaced.")
    return state


def _unregister_runtime_socket(state: _RuntimeSocketState) -> None:
    with _RUNTIME_SOCKETS_LOCK:
        if _RUNTIME_SOCKETS.get(state.device_id) is state:
            _RUNTIME_SOCKETS.pop(state.device_id, None)
    state.fail_all("Computer Runtime connection ended.")


def runtime_terminal_stream_is_local(*, device_id: str, generation: int) -> bool:
    """Return whether this ASGI worker owns the authenticated Runtime socket."""

    with _RUNTIME_SOCKETS_LOCK:
        socket = _RUNTIME_SOCKETS.get(str(device_id))
        return bool(socket is not None and not socket.closed and socket.generation == int(generation))


def open_runtime_terminal_stream(
    *,
    connection,
    shell: str,
    workspace_root: str,
    cols: int,
    rows: int,
) -> RuntimeTerminalStreamChannel:
    device = ensure_runtime_connection(connection, operation="terminal.open")
    if int((device.capabilities or {}).get("terminal.stream.v1") or 0) < 1:
        raise ComputerCapabilityUnavailable(
            "Nexus Computer Runtime does not provide terminal.stream.v1; update and restart the Runtime."
        )
    with _RUNTIME_SOCKETS_LOCK:
        socket = _RUNTIME_SOCKETS.get(str(device.id))
    if socket is None or socket.generation != int(device.generation):
        raise ComputerRuntimeOffline("Nexus Computer Runtime streaming connection is not available on this Cloud worker.")
    stream = socket.create_stream()
    try:
        socket.send({
            "version": 1,
            "type": "terminal_stream_open",
            "stream_id": stream.stream_id,
            "shell": str(shell),
            "workspace_root": str(workspace_root),
            "cols": max(1, min(int(cols), 1000)),
            "rows": max(1, min(int(rows), 1000)),
        })
        if not stream.opened.wait(timeout=10.0):
            raise ComputerRuntimeOffline("Nexus Computer Runtime did not open the terminal stream in time.")
        if stream.error:
            raise ComputerRuntimeOffline(stream.error)
        if stream.closed:
            raise ComputerRuntimeOffline("Nexus Computer Runtime closed the terminal stream during startup.")
        return RuntimeTerminalStreamChannel(socket=socket, stream=stream)
    except Exception:
        socket.remove_stream(stream.stream_id)
        raise


async def computer_runtime_websocket(*, scope, receive, send) -> None:
    token = _bearer_token(scope)
    if not token:
        await send({"type": "websocket.close", "code": 4401})
        return
    try:
        device = await sync_to_async(consume_connect_ticket, thread_sensitive=False)(token=token)
    except Exception:
        await send({"type": "websocket.close", "code": 4401})
        return
    device_id = str(device.id)
    generation = int(device.generation)
    await send({"type": "websocket.accept"})
    await _send_json(send, {
        "version": 1,
        "type": "connected",
        "device_id": device_id,
        "generation": generation,
    })

    runtime_socket = _register_runtime_socket(device_id=device_id, generation=generation)
    wake_event = asyncio.Event()
    redis_task = asyncio.create_task(_redis_listener(device_id=device_id, wake_event=wake_event))
    receive_task = asyncio.create_task(receive())
    stream_send_task = asyncio.create_task(runtime_socket.outgoing.get())
    try:
        while True:
            if not await sync_to_async(runtime_generation_is_current, thread_sensitive=False)(
                device_id=device_id,
                generation=generation,
            ):
                await send({"type": "websocket.close", "code": 4409})
                return
            commands = await sync_to_async(claim_pending_commands, thread_sensitive=False)(device_id=device_id)
            for command in commands:
                await _send_json(send, command)
            cancellations = await sync_to_async(claim_cancel_frames, thread_sensitive=False)(device_id=device_id)
            for cancellation in cancellations:
                await _send_json(send, cancellation)

            wake_wait = asyncio.create_task(wake_event.wait())
            done, _pending = await asyncio.wait(
                {receive_task, wake_wait, stream_send_task},
                timeout=0.5,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stream_send_task in done:
                await _send_json(send, stream_send_task.result())
                while True:
                    try:
                        queued_stream_frame = runtime_socket.outgoing.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    await _send_json(send, queued_stream_frame)
                stream_send_task = asyncio.create_task(runtime_socket.outgoing.get())
            if wake_wait in done:
                wake_event.clear()
            else:
                wake_wait.cancel()
                await asyncio.gather(wake_wait, return_exceptions=True)
            if receive_task not in done:
                continue
            event = receive_task.result()
            if event.get("type") == "websocket.disconnect":
                return
            receive_task = asyncio.create_task(receive())
            if event.get("type") != "websocket.receive":
                continue
            frame = _parse_frame(event)
            if not frame:
                await send({"type": "websocket.close", "code": 4400})
                return
            try:
                accepted_sequence = runtime_socket.accept_sequence(frame)
            except ComputerRuntimeError as exc:
                await _send_json(send, {
                    "version": 1,
                    "type": "protocol_error",
                    "code": str(getattr(exc, "default_code", "COMPUTER_RUNTIME_ERROR")),
                    "message": str(exc.detail),
                })
                await send({"type": "websocket.close", "code": 4400})
                return
            if not accepted_sequence:
                continue
            if str(frame.get("type") or "") in {
                "terminal_stream_opened",
                "terminal_stream_output",
                "terminal_stream_error",
                "terminal_stream_closed",
            }:
                runtime_socket.deliver(frame)
                continue
            try:
                outcome = await sync_to_async(apply_runtime_frame, thread_sensitive=False)(
                    device_id=device_id,
                    generation=generation,
                    frame=frame,
                )
            except ComputerRuntimeError as exc:
                await _send_json(send, {
                    "version": 1,
                    "type": "protocol_error",
                    "code": str(getattr(exc, "default_code", "COMPUTER_RUNTIME_ERROR")),
                    "message": str(exc.detail),
                })
                await send({"type": "websocket.close", "code": 4400})
                return
            reply_type = "hello_ack" if outcome.get("hello") else "heartbeat_ack" if outcome.get("heartbeat") else "frame_ack"
            await _send_json(send, {
                "version": 1,
                "type": reply_type,
                "generation": generation,
                **outcome,
            })
    finally:
        receive_task.cancel()
        stream_send_task.cancel()
        redis_task.cancel()
        await asyncio.gather(receive_task, stream_send_task, redis_task, return_exceptions=True)
        _unregister_runtime_socket(runtime_socket)


async def _redis_listener(*, device_id: str, wake_event: asyncio.Event) -> None:
    client = None
    pubsub = None
    try:
        import redis.asyncio as redis_async

        client = redis_async.Redis.from_url(str(settings.REDIS_URL), socket_connect_timeout=1, socket_timeout=1)
        pubsub = client.pubsub()
        await pubsub.subscribe(f"nexus:computer-runtime:{device_id}")
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message is not None:
                wake_event.set()
            await asyncio.sleep(0.02)
    except asyncio.CancelledError:
        raise
    except Exception:
        # The durable command table and the socket loop's periodic poll preserve delivery.
        while True:
            await asyncio.sleep(1.0)
            wake_event.set()
    finally:
        if pubsub is not None:
            await pubsub.aclose()
        if client is not None:
            await client.aclose()


def _bearer_token(scope) -> str:
    for key, value in scope.get("headers", []):
        if key.lower() != b"authorization":
            continue
        text = value.decode("latin1")
        if text.lower().startswith("bearer "):
            return text[7:].strip()
    return ""


def _parse_frame(event: dict) -> dict:
    text = event.get("text")
    if text is None and event.get("bytes") is not None:
        try:
            text = event["bytes"].decode("utf-8")
        except UnicodeDecodeError:
            return {}
    if not text or len(text.encode("utf-8")) > 2_500_000:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) and value.get("version") == 1 else {}


async def _send_json(send, payload: dict) -> None:
    await send({"type": "websocket.send", "text": json.dumps(payload, separators=(",", ":"), ensure_ascii=False)})
