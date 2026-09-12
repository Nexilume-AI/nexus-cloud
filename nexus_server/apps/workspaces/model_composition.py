"""Explicit shared integration modules, independent of device execution tables."""
from importlib import import_module
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def load_integration_models():
    modules = getattr(settings, "NEXUS_WORKSPACE_MODEL_MODULES", None)
    if not isinstance(modules, (tuple, list)) or any(not isinstance(name, str) or not name for name in modules):
        raise ImproperlyConfigured("Workspace model integrations must be explicitly configured.")
    if len(set(modules)) != len(modules):
        raise ImproperlyConfigured("Workspace model integrations must not be duplicated.")
    for name in modules:
        try:
            import_module(name)
        except (ImportError, ValueError):
            raise ImproperlyConfigured("Workspace model integration is unavailable.") from None
