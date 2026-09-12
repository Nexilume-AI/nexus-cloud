"""Host-selected Agent presentation. Requests cannot choose an edition."""
from importlib import import_module
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from rest_framework.serializers import BaseSerializer


def serializer_export(name):
    path = getattr(settings, "NEXUS_AGENT_SERIALIZERS_MODULE", "")
    if not isinstance(path, str) or not path:
        raise ImproperlyConfigured("Nexus Agent presentation is not configured.")
    try:
        module = import_module(path)
    except ImportError:
        raise ImproperlyConfigured("Nexus Agent presentation could not be loaded.") from None
    return getattr(module, name)


def configured_serializer(name):
    try:
        serializer = serializer_export(name)
        if not isinstance(serializer, type) or not issubclass(serializer, BaseSerializer):
            raise TypeError
    except (TypeError, AttributeError):
        raise ImproperlyConfigured("Nexus Agent serializer is invalid.") from None
    return serializer
