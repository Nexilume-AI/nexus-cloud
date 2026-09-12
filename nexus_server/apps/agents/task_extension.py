"""Explicit optional task registrations selected by the application host."""
from importlib import import_module
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def load_task_extensions():
    paths = getattr(settings, 'NEXUS_AGENT_EXTRA_TASK_MODULES', None)
    if not isinstance(paths, tuple) or any(not isinstance(path, str) or not path for path in paths):
        raise ImproperlyConfigured('Agent task modules must be explicitly configured.')
    try:
        return tuple(import_module(path) for path in paths)
    except (ImportError, AttributeError, ValueError):
        raise ImproperlyConfigured('Agent task extension is unavailable.') from None
