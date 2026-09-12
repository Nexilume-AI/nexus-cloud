"""Compatibility path for an explicitly composed legacy notification host.

Community uses the shared Inbox. Importing this legacy API must not load private
authorization implicitly. Module identity preserves existing URL and patch users.
"""
import sys
from importlib import import_module

from django.core.exceptions import ImproperlyConfigured
from rest_framework.views import APIView
from .policy import backend


def _configured_legacy_views():
    path = getattr(backend(), "legacy_views_module", None)
    if not isinstance(path, str) or not path or path == "apps.notifications.views":
        raise ImproperlyConfigured("Legacy notification HTTP host is not configured.")
    try:
        module = import_module(path)
    except ImportError:
        raise ImproperlyConfigured("Legacy notification HTTP host could not be loaded.") from None
    required = ("NotificationListView", "NotificationSummaryView", "NotificationReadView",
                "NotificationReadAllView", "NotificationOpenView")
    for name in required:
        view = getattr(module, name, None)
        if not isinstance(view, type) or not issubclass(view, APIView):
            raise ImproperlyConfigured("Legacy notification HTTP host is incomplete.")
    return module


sys.modules[__name__] = _configured_legacy_views()
