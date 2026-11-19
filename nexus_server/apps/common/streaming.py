"""Bounded sync-to-async streaming, without Django's eager ASGI conversion."""
from __future__ import annotations

import asyncio
import queue
import threading


class AsyncStreamBridge:
    """Consume a sync iterator in one worker; keep ORM/thread context stable.

    The cancellation callback must be non-blocking. The producer owns iterator
    cleanup (closing an executing Python generator on another thread is unsafe).
    """

    def __init__(self, chunks, *, cancel=None, capacity=8, heartbeat_seconds=None):
        if capacity < 1:
            raise ValueError("Stream capacity must be positive")
        self.chunks = iter(chunks)
        self.cancel = cancel
        self.pending = queue.Queue(maxsize=capacity)
        self.stopped = threading.Event()
        self._close_lock = threading.Lock()
        self.heartbeat_seconds = heartbeat_seconds

    def close(self):
        with self._close_lock:
            if self.stopped.is_set():
                return
            self.stopped.set()
            if self.cancel is not None:
                self.cancel()

    async def __aiter__(self):
        loop = asyncio.get_running_loop()
        ready = asyncio.Event()

        def enqueue(kind, value):
            while not self.stopped.is_set():
                try:
                    self.pending.put((kind, value), timeout=0.1)
                except queue.Full:
                    continue
                try:
                    loop.call_soon_threadsafe(ready.set)
                except RuntimeError:  # Server/event loop has shut down.
                    self.close()
                return

        def produce():
            try:
                for chunk in self.chunks:
                    if self.stopped.is_set():
                        break
                    enqueue("chunk", chunk)
                enqueue("done", None)
            except BaseException as exc:
                enqueue("error", exc)
            finally:
                close = getattr(self.chunks, "close", None)
                try:
                    if close is not None:
                        close()
                finally:
                    # A dedicated worker must not leave its ORM connection open.
                    from django.conf import settings
                    if settings.configured:
                        from django.db import connections
                        connections.close_all()

        threading.Thread(target=produce, name="nexus-asgi-stream", daemon=True).start()
        boundary = True
        suffix = ""
        try:
            while not self.stopped.is_set():
                ready.clear()
                try:
                    kind, value = self.pending.get_nowait()
                except queue.Empty:
                    try:
                        await asyncio.wait_for(ready.wait(), self.heartbeat_seconds)
                    except asyncio.TimeoutError:
                        # Never insert a comment inside a fragmented SSE event.
                        if boundary:
                            yield ": nexus-keepalive\n\n"
                    continue
                if kind == "chunk":
                    if self.heartbeat_seconds is not None:
                        text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
                        suffix = (suffix + text)[-4:]
                        boundary = suffix.endswith("\n\n") or suffix.endswith("\r\n\r\n")
                    yield value
                elif kind == "error":
                    raise value
                else:
                    break
        finally:
            self.close()
