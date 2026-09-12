"""Legacy Computer facade for an explicitly configured distribution host.

Personal uses shared Runtime execution and its own Tool Setup implementation.
This module must never implicitly load legacy credentials or SSH services.
"""
import sys
from importlib import import_module

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _configured_legacy_services():
    path = getattr(settings, "NEXUS_WORKSPACE_LEGACY_SERVICES_MODULE", None)
    if not isinstance(path, str) or not path or path == "apps.workspaces.services":
        raise ImproperlyConfigured("Legacy Computer services host is not configured.")
    try:
        module = import_module(path)
    except ImportError:
        raise ImproperlyConfigured("Legacy Computer services host could not be loaded.") from None
    required = (
        "workspace_runner", "create_workspace_connection", "test_workspace_connection",
        "validate_workspace_connection", "get_workspace_connection",
        "get_workspace_tool_config", "get_workspace_tool_config_options",
        "preview_workspace_tool_config", "apply_workspace_tool_config_change",
        "apply_workspace_tool_config", "rollback_workspace_tool_config",
    )
    if not all(callable(getattr(module, name, None)) for name in required):
        raise ImproperlyConfigured("Legacy Computer services host is incomplete.")
    return module


# Preserve legacy imports and globals patched by existing callers and tests.
sys.modules[__name__] = _configured_legacy_services()
