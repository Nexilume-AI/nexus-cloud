"""Explicit metric window implementation; never guess a financial data model."""
from inspect import signature
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


def configured_window():
    path = getattr(settings, "NEXUS_MONITORING_WINDOW_FUNCTION", "")
    if (not isinstance(path, str) or not path or path.startswith(__name__ + ".")
            or path == "apps.metrics.registry.window_metrics"):
        raise ImproperlyConfigured("Nexus monitoring window is not configured.")
    try:
        function = import_string(path)
        signature(function).bind(object(), start=None, end=None, seconds=300,
            project_id="", resource_type="", resource_id="")
    except (ImportError, AttributeError, TypeError, ValueError):
        raise ImproperlyConfigured("Nexus monitoring window is invalid.") from None
    return function
