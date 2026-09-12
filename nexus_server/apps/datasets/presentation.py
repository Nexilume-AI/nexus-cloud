"""Host-selected Dataset UI/API surfaces, never chosen by request input."""
from importlib import import_module
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.urls import URLPattern, URLResolver
from rest_framework.serializers import BaseSerializer


def _module(setting):
    path = getattr(settings, setting, "")
    if not isinstance(path, str) or not path:
        raise ImproperlyConfigured("Nexus Dataset presentation is not configured.")
    try:
        return import_module(path)
    except ImportError:
        raise ImproperlyConfigured("Nexus Dataset presentation could not be loaded.") from None


def serializer_export(name):
    return getattr(_module("NEXUS_DATASET_SERIALIZERS_MODULE"), name)


def configured_dataset_serializer():
    try:
        serializer = serializer_export("DatasetSerializer")
        if not isinstance(serializer, type) or not issubclass(serializer, BaseSerializer):
            raise TypeError
        return serializer
    except (AttributeError, TypeError):
        raise ImproperlyConfigured("Nexus Dataset serializer is invalid.") from None


def view_export(name):
    return getattr(_module("NEXUS_DATASET_VIEWS_MODULE"), name)


def configured_dataset_urls():
    patterns = getattr(_module("NEXUS_DATASET_URLCONF"), "urlpatterns", None)
    if not isinstance(patterns, (list, tuple)) or not all(isinstance(p, (URLPattern, URLResolver)) for p in patterns):
        raise ImproperlyConfigured("Nexus Dataset URL configuration is invalid.")
    return patterns
