"""Bound a decision's elapsed time, including DNS and slowly arriving bodies."""
import socket
import threading
import time

# A stuck OS resolver cannot be killed safely. Bound and daemonize these workers;
# expiration prevents a resolver that returns late from dispatching a request.
_slots = threading.BoundedSemaphore(32)


class RequestDeadline:
    def __init__(self, seconds):
        self.end = time.monotonic() + seconds
        self.expired = threading.Event()
        self.lock = threading.Lock()
        self.sockets = []

    def remaining(self):
        value = self.end - time.monotonic()
        if self.expired.is_set() or value <= 0:
            raise TimeoutError('Decision deadline exceeded')
        return value

    def watch(self, sock):
        with self.lock:
            self.remaining()
            self.sockets.append(sock)
            sock.settimeout(self.remaining())

    def abort(self):
        self.expired.set()
        with self.lock:
            sockets, self.sockets = self.sockets, []
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def run(self, action):
        if not _slots.acquire(blocking=False):
            raise TimeoutError('Decision workers busy')
        done = threading.Event()
        result = []

        def worker():
            try:
                self.remaining()
                result.append((True, action()))
            except Exception as exc:
                result.append((False, exc))
            finally:
                _slots.release()
                done.set()

        thread = threading.Thread(target=worker, daemon=True, name='nexus-decision')
        try:
            thread.start()
        except Exception:
            _slots.release()
            raise
        try:
            if not done.wait(self.remaining()):
                raise TimeoutError('Decision deadline exceeded')
            self.remaining()
            ok, value = result[0]
            if not ok:
                raise value
            return value
        finally:
            self.abort()
