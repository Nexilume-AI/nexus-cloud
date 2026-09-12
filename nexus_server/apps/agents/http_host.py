"""Explicit process-level composition, never selected by request input."""
from importlib import import_module
from types import ModuleType
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.urls import URLPattern, URLResolver
from rest_framework.views import APIView


def _module(setting):
    path = getattr(settings, setting, "")
    if (not isinstance(path, str) or not path
            or path in ("apps.agents.views", "apps.agents.urls",
                        "apps.agents.runtime_views", "apps.agents.runtime_urls", __name__)):
        raise ImproperlyConfigured("Nexus Agent HTTP host is not configured.")
    try:
        module = import_module(path)
    except ImportError:
        raise ImproperlyConfigured("Nexus Agent HTTP host could not be loaded.") from None
    if not isinstance(module, ModuleType):
        raise ImproperlyConfigured("Nexus Agent HTTP host is invalid.")
    return module


def configured_views():
    return _views("NEXUS_AGENT_VIEWS_MODULE", "AgentListCreateView")


def configured_runtime_views():
    return _views("NEXUS_AGENT_RUNTIME_VIEWS_MODULE", "AgentMCPProxyView")


def _views(setting, required_view):
    module = _module(setting)
    view = getattr(module, required_view, None)
    if not isinstance(view, type) or not issubclass(view, APIView):
        raise ImproperlyConfigured("Nexus Agent HTTP views are invalid.")
    return module


def configured_urls():
    return _urls("NEXUS_AGENT_URLCONF")


def configured_runtime_urls():
    return _urls("NEXUS_AGENT_RUNTIME_URLCONF")


def _urls(setting):
    module = _module(setting)
    patterns = getattr(module, "urlpatterns", None)
    if not isinstance(patterns, (list, tuple)) or not all(
            isinstance(item, (URLPattern, URLResolver)) for item in patterns):
        raise ImproperlyConfigured("Nexus Agent HTTP URLs are invalid.")
    return patterns
