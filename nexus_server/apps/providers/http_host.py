"""Host-selected legacy HTTP composition; no implicit commercial endpoints."""
from importlib import import_module
from types import ModuleType
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.urls import URLPattern, URLResolver
from rest_framework.views import APIView
from rest_framework.serializers import BaseSerializer


def _module(setting):
    path = getattr(settings, setting, "")
    if (not isinstance(path, str) or not path
            or path in ("apps.providers.views", "apps.providers.urls",
                        "apps.providers.serializers", __name__)):
        raise ImproperlyConfigured("Nexus Provider HTTP host is not configured.")
    try:
        module = import_module(path)
    except ImportError:
        raise ImproperlyConfigured("Nexus Provider HTTP host could not be loaded.") from None
    if not isinstance(module, ModuleType):
        raise ImproperlyConfigured("Nexus Provider HTTP host is invalid.")
    return module


def configured_views():
    module = _module("NEXUS_PROVIDER_VIEWS_MODULE")
    view = getattr(module, "ProviderListView", None)
    if not isinstance(view, type) or not issubclass(view, APIView):
        raise ImproperlyConfigured("Nexus Provider HTTP views are invalid.")
    return module


def configured_urls():
    patterns = getattr(_module("NEXUS_PROVIDER_URLCONF"), "urlpatterns", None)
    if not isinstance(patterns, (list, tuple)) or not all(
            isinstance(item, (URLPattern, URLResolver)) for item in patterns):
        raise ImproperlyConfigured("Nexus Provider HTTP URLs are invalid.")
    return patterns


def configured_serializers():
    module = _module("NEXUS_PROVIDER_SERIALIZERS_MODULE")
    serializer = getattr(module, "ProviderConnectionSerializer", None)
    if not isinstance(serializer, type) or not issubclass(serializer, BaseSerializer):
        raise ImproperlyConfigured("Nexus Provider serializers are invalid.")
    return module
