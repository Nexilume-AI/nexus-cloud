"""Required, host-selected Inbox composition; requests cannot select a host."""
from importlib import import_module
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


def backend():
    path = getattr(settings, "NEXUS_NOTIFICATION_BACKEND", "")
    try:
        value = import_string(path)()
    except (ImportError, AttributeError, TypeError, ValueError):
        raise ImproperlyConfigured("Notification host is not configured.") from None
    methods = ("visible_items", "authorized_project_ids", "user_can_receive_role_item",
               "role_subscriber_ids", "refresh_external_item", "navigation_routes", "export_source", "query_sources",
               "maintenance_queryset", "record_operational_issue", "reconcile_extra_sources")
    modules = getattr(value, "additional_signal_modules", None)
    if (not all(callable(getattr(value, name, None)) for name in methods)
            or not isinstance(modules, tuple) or not all(isinstance(name, str) and name for name in modules)):
        raise ImproperlyConfigured("Notification host is incomplete.")
    return value


def load_signal_modules():
    for name in backend().additional_signal_modules:
        try:
            import_module(name)
        except ImportError:
            raise ImproperlyConfigured("Notification signals could not be loaded.") from None
