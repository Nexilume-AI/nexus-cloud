"""Explicit distribution composition. No guessed private or empty backend."""
from importlib import import_module
from types import ModuleType
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.urls import URLPattern, URLResolver
from rest_framework.views import APIView


COMPONENTS = {
    "services": ("get_system_metrics", "get_resource_metrics", "list_alerts", "create_alert", "delete_alert"),
    "registry": ("canonical", "definitions", "validate", "window_metrics", "resolve"),
    "views": ("MetricsView", "AlertListCreateView", "AlertEventListView"),
    "urls": (),
    "local": ("jobs",),
    "tasks": ("capture_metric_snapshots_task", "evaluate_alert_rules_task", "deliver_monitoring_notifications_task", "cleanup_monitoring_task"),
}


def configured_component(name):
    paths = getattr(settings, "NEXUS_MONITORING_COMPONENTS", None)
    if name not in COMPONENTS or not isinstance(paths, dict):
        raise ImproperlyConfigured("Nexus monitoring components are not configured.")
    path = paths.get(name)
    if (not isinstance(path, str) or not path or path == __name__
            or path in {f"apps.metrics.{key}" for key in COMPONENTS}):
        raise ImproperlyConfigured("Nexus monitoring component is not configured.")
    try:
        module = import_module(path)
    except ImportError:
        raise ImproperlyConfigured("Nexus monitoring component could not be loaded.") from None
    if not isinstance(module, ModuleType):
        raise ImproperlyConfigured("Nexus monitoring component is invalid.")
    for symbol in COMPONENTS[name]:
        member = getattr(module, symbol, None)
        if not callable(member) or (name == "views" and (not isinstance(member, type) or not issubclass(member, APIView))):
            raise ImproperlyConfigured("Nexus monitoring component is incomplete.")
    if name == "registry" and not all(isinstance(getattr(module, key, None), dict) for key in ("DEFINITIONS", "ALIASES")):
        raise ImproperlyConfigured("Nexus monitoring registry is invalid.")
    if name == "urls":
        patterns = getattr(module, "urlpatterns", None)
        if not isinstance(patterns, (list, tuple)) or not patterns or not all(
                isinstance(item, (URLPattern, URLResolver)) for item in patterns):
            raise ImproperlyConfigured("Nexus monitoring URL configuration is invalid.")
    return module
