"""Explicit Router serializer selection and response-context policy."""
from importlib import import_module
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from rest_framework.serializers import BaseSerializer


EXPORTS = frozenset(("RouterOutputSerializer", "RouterChildBindingSerializer", "RouterSerializer",
    "RouterCreateSerializer", "RouterUpdateSerializer", "RouterOutputUpdateSerializer",
    "RouterChildBindingCreateSerializer", "RouterChildBindingUpdateSerializer", "RouterVersionSerializer",
    "RouterDeploymentSerializer", "RouterModelGroupBindingSerializer", "RouterModelGroupBindSerializer",
    "RouterPolicySerializer", "RouterPricingSerializer", "RouterPricingSetSerializer",
    "RouterProviderPreferenceSerializer", "RouterProviderPreferenceCreateSerializer"))


def _module():
    try:
        module = import_module(getattr(settings, "NEXUS_ROUTER_SERIALIZERS_MODULE", ""))
    except (ImportError, AttributeError, TypeError, ValueError):
        raise ImproperlyConfigured("Router presentation is not configured.") from None
    if not callable(getattr(module, "response_context", None)):
        raise ImproperlyConfigured("Router response context is not configured.")
    return module


def configured_serializer(name):
    if name not in EXPORTS:
        raise ImproperlyConfigured("Unknown Router serializer.")
    value = getattr(_module(), name, None)
    if not isinstance(value, type) or not issubclass(value, BaseSerializer):
        raise ImproperlyConfigured("Router serializer is unavailable in this distribution.")
    return value


def response_context(request):
    value = _module().response_context(request)
    if not isinstance(value, dict):
        raise ImproperlyConfigured("Invalid Router response context.")
    return value
