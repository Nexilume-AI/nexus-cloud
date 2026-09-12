"""Explicit tenancy implementation hosts; never infer management authority."""
from importlib import import_module
from types import ModuleType
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.urls import URLPattern, URLResolver


KINDS = ("services", "serializers", "views", "permissions", "urls")


def configured_module(kind):
    if kind not in KINDS:
        raise ImproperlyConfigured("Nexus tenancy host kind is invalid.")
    path = getattr(settings, "NEXUS_TENANCY_" + kind.upper() + "_MODULE", "")
    recursive = {__name__, *("apps.tenancy." + item for item in KINDS)}
    if not isinstance(path, str) or not path or path in recursive:
        raise ImproperlyConfigured("Nexus tenancy host is not configured.")
    try:
        module = import_module(path)
    except ImportError:
        raise ImproperlyConfigured("Nexus tenancy host could not be loaded.") from None
    if not isinstance(module, ModuleType):
        raise ImproperlyConfigured("Nexus tenancy host is invalid.")
    if kind == "services" and not callable(getattr(module, "get_tenant_from_request", None)):
        raise ImproperlyConfigured("Nexus tenancy request context is missing.")
    required = {"views": "TenantListCreateView", "serializers": "TenantSerializer",
                "permissions": "IsTenantAdmin"}.get(kind)
    if required and not isinstance(getattr(module, required, None), type):
        raise ImproperlyConfigured("Nexus tenancy implementation is incomplete.")
    return module


def configured_urls():
    patterns = getattr(configured_module("urls"), "urlpatterns", None)
    if not isinstance(patterns, (list, tuple)) or not all(
            isinstance(item, (URLPattern, URLResolver)) for item in patterns):
        raise ImproperlyConfigured("Nexus tenancy URL host is invalid.")
    return patterns
