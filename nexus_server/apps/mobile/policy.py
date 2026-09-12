"""Required host context for personal devices; never replaces device tokens."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


def _backend():
    path = getattr(settings, "NEXUS_MOBILE_CONTEXT_BACKEND", "")
    if not isinstance(path, str) or not path:
        raise ImproperlyConfigured("Mobile context policy is not configured.")
    try:
        backend = import_string(path)()
    except (ImportError, AttributeError, TypeError):
        raise ImproperlyConfigured("Mobile context policy could not be loaded.") from None
    if not all(callable(getattr(backend, name, None)) for name in ("resolve_project", "device_context_valid", "visible_devices")):
        raise ImproperlyConfigured("Mobile context policy is incomplete.")
    return backend


def resolve_device_project(*, request, tenant, project_id):
    return _backend().resolve_project(request=request, tenant=tenant, project_id=project_id)


def device_context_valid(device):
    return _backend().device_context_valid(device) is True


def scope_mobile_devices(*, request, queryset):
    return _backend().visible_devices(request=request, queryset=queryset)
