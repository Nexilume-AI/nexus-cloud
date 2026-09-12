"""Django connection lifetime for database work outside the HTTP lifecycle."""
from asgiref.sync import SyncToAsync
from django.db import connections


def close_idle_connections():
    for connection in connections.all(initialized_only=True):
        # Never close a caller-owned transaction (including an async test
        # invoked inside TestCase.atomic); its outer scope owns cleanup.
        if not connection.in_atomic_block:
            connection.close_if_unusable_or_obsolete()


class DatabaseSyncToAsync(SyncToAsync):
    def thread_handler(self, *args, **kwargs):
        close_idle_connections()
        try:
            return super().thread_handler(*args, **kwargs)
        finally:
            close_idle_connections()


def database_sync_to_async(func=None, *, thread_sensitive=True):
    if func is None:
        return lambda wrapped: DatabaseSyncToAsync(wrapped, thread_sensitive=thread_sensitive)
    return DatabaseSyncToAsync(func, thread_sensitive=thread_sensitive)
