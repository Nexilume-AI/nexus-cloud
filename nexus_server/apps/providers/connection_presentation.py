"""Required host presentation for operational Provider connections."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string
from rest_framework.serializers import BaseSerializer


def connection_serializer():
    try:
        cls = import_string(getattr(settings, "NEXUS_PROVIDER_CONNECTION_SERIALIZER", ""))
    except (ImportError, AttributeError, TypeError, ValueError):
        raise ImproperlyConfigured("Provider connection presentation is not configured.") from None
    if not isinstance(cls, type) or not issubclass(cls, BaseSerializer):
        raise ImproperlyConfigured("Provider connection presentation is invalid.")
    return cls
