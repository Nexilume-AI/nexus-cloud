"""Require a complete operational worker backend without choosing a distribution."""
from importlib import import_module
from types import ModuleType
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def configured_workers():
    path = getattr(settings, "NEXUS_MONITORING_WORKER_MODULE", "")
    if not isinstance(path, str) or not path or path in (__name__, "apps.metrics.operations"):
        raise ImproperlyConfigured("Nexus monitoring workers are not configured.")
    try:
        module = import_module(path)
    except ImportError:
        raise ImproperlyConfigured("Nexus monitoring workers could not be loaded.") from None
    if not isinstance(module, ModuleType) or not all(callable(getattr(module, name, None))
            for name in ("collect_snapshots", "evaluate_rules", "deliver_notifications", "cleanup_monitoring")):
        raise ImproperlyConfigured("Nexus monitoring workers are incomplete.")
    return module
