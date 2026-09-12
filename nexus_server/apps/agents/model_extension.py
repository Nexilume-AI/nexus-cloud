"""Required host selection for private additions to the Agent schema."""
from importlib import import_module
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def model_extension():
    path = getattr(settings, "NEXUS_AGENT_MODEL_EXTENSION", "")
    if not isinstance(path, str) or not path:
        raise ImproperlyConfigured("Agent model extension must be explicitly configured.")
    try:
        module = import_module(path)
    except (ImportError, AttributeError, ValueError):
        raise ImproperlyConfigured("Agent model extension is unavailable.") from None
    if not callable(getattr(module, "register_models", None)):
        raise ImproperlyConfigured("Agent model extension is incomplete.")
    return module
