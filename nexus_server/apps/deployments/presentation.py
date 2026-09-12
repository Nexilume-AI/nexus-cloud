"""Source/Pool HTTP presentation is selected by the host, never by a request."""
from importlib import import_module
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from rest_framework.serializers import BaseSerializer


EXPORTS = {"DeploymentSerializer", "DeploymentCreateSerializer", "ModelSourceBatchSerializer",
           "ModelGroupDeploymentSerializer", "ModelGroupSerializer"}


def presentation_module():
    path = getattr(settings, "NEXUS_DEPLOYMENT_SERIALIZERS_MODULE", "")
    if not isinstance(path, str) or not path:
        raise ImproperlyConfigured("Source presentation is not configured.")
    try:
        module = import_module(path)
    except ImportError:
        raise ImproperlyConfigured("Source presentation could not be loaded.") from None
    if not callable(getattr(module, "response_context", None)):
        raise ImproperlyConfigured("Source response context is not configured.")
    return module


def configured_serializer(name):
    if name not in EXPORTS:
        raise ImproperlyConfigured("Unknown Source serializer.")
    cls = getattr(presentation_module(), name, None)
    if not isinstance(cls, type) or not issubclass(cls, BaseSerializer):
        raise ImproperlyConfigured("Source serializer is invalid.")
    return cls


def response_context(request):
    result = presentation_module().response_context(request)
    if not isinstance(result, dict):
        raise ImproperlyConfigured("Source response context is invalid.")
    return result
