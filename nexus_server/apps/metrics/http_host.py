"""Required operational HTTP backend. Never default to a no-op or private host."""
from importlib import import_module
from types import ModuleType
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

OPERATIONS = ("get_resource_metrics", "get_system_metrics", "definitions", "list_alerts",
    "create_alert", "delete_alert", "list_alert_events", "get_alert_event", "test_alert", "action_target")


def backend():
    path = getattr(settings, "NEXUS_MONITORING_HTTP_MODULE", "")
    if not isinstance(path, str) or not path or path in (__name__, "apps.metrics.views", "apps.metrics.operational_views"):
        raise ImproperlyConfigured("Nexus monitoring HTTP backend is not configured.")
    try:
        module = import_module(path)
    except ImportError:
        raise ImproperlyConfigured("Nexus monitoring HTTP backend could not be loaded.") from None
    if not isinstance(module, ModuleType) or not all(callable(getattr(module, name, None)) for name in OPERATIONS):
        raise ImproperlyConfigured("Nexus monitoring HTTP backend is incomplete.")
    return module


def __getattr__(name):
    if name in OPERATIONS:
        return getattr(backend(), name)
    raise AttributeError(name)
