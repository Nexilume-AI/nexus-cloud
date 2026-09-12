"""Shared Tool Setup API dispatch; no implicit commercial fallback."""
from importlib import import_module
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

OPERATIONS = ["get_workspace_tool_config","get_workspace_tool_config_options","preview_workspace_tool_config","apply_workspace_tool_config_change","apply_workspace_tool_config","rollback_workspace_tool_config"]


def tool_setup_backend():
    path = getattr(settings, "NEXUS_TOOL_SETUP_MODULE", "")
    try:
        if not isinstance(path, str) or not path:
            raise ValueError()
        module = import_module(path)
    except (ImportError, ValueError, TypeError):
        raise ImproperlyConfigured("Tool Setup integration is unavailable.") from None
    if not all(callable(getattr(module, name, None)) for name in OPERATIONS):
        raise ImproperlyConfigured("Tool Setup integration is incomplete.")
    return module


def get_workspace_tool_config(**kwargs):
    return tool_setup_backend().get_workspace_tool_config(**kwargs)


def get_workspace_tool_config_options(**kwargs):
    return tool_setup_backend().get_workspace_tool_config_options(**kwargs)


def preview_workspace_tool_config(**kwargs):
    return tool_setup_backend().preview_workspace_tool_config(**kwargs)


def apply_workspace_tool_config_change(**kwargs):
    return tool_setup_backend().apply_workspace_tool_config_change(**kwargs)


def apply_workspace_tool_config(**kwargs):
    return tool_setup_backend().apply_workspace_tool_config(**kwargs)


def rollback_workspace_tool_config(**kwargs):
    return tool_setup_backend().rollback_workspace_tool_config(**kwargs)
