"""Trace storage/presentation is selected explicitly, never via private fallback."""
from importlib import import_module
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def trace_backend():
    path = getattr(settings, "NEXUS_ROUTER_TRACES_MODULE", "")
    try:
        if not isinstance(path, str) or not path:
            raise ValueError()
        module = import_module(path)
    except (ImportError, ValueError, TypeError):
        raise ImproperlyConfigured("Router traces are not configured.") from None
    if not all(callable(getattr(module, name, None)) for name in ("list_router_traces", "serialize_router_trace")):
        raise ImproperlyConfigured("Router traces are incomplete.")
    return module
