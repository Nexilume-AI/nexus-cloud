"""Load optional report serializers only through an explicit host setting."""
from importlib import import_module
from types import ModuleType
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from rest_framework.serializers import BaseSerializer


def report_serializers():
    path = getattr(settings, "NEXUS_MONITORING_REPORT_SERIALIZERS_MODULE", "")
    if not isinstance(path, str) or not path or path in ("apps.metrics.serializers", __name__):
        raise ImproperlyConfigured("Nexus monitoring report serializers are not configured.")
    try:
        module = import_module(path)
    except ImportError:
        raise ImproperlyConfigured("Nexus monitoring report serializers could not be loaded.") from None
    if not isinstance(module, ModuleType):
        raise ImproperlyConfigured("Nexus monitoring report serializers are invalid.")
    for name in ("ReportScheduleSerializer", "ReportScheduleCreateSerializer", "ReportDeliverySerializer"):
        serializer = getattr(module, name, None)
        if not isinstance(serializer, type) or not issubclass(serializer, BaseSerializer):
            raise ImproperlyConfigured("Nexus monitoring report serializers are incomplete.")
    return module
