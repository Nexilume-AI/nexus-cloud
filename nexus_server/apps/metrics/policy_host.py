"""Select monitoring authority without importing a collaboration implementation."""
from importlib import import_module
from types import ModuleType
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def configured_policy():
    path = getattr(settings, "NEXUS_MONITORING_POLICY_MODULE", "")
    if not isinstance(path, str) or not path or path in ("apps.metrics.policy", __name__):
        raise ImproperlyConfigured("Nexus monitoring policy is not configured.")
    try:
        module = import_module(path)
    except ImportError:
        raise ImproperlyConfigured("Nexus monitoring policy could not be loaded.") from None
    if not isinstance(module, ModuleType) or not all(callable(getattr(module, name, None))
            for name in ("context", "financial", "capabilities", "redact_financial")):
        raise ImproperlyConfigured("Nexus monitoring policy is incomplete.")
    return module
