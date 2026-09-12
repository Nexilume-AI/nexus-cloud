"""Required Router distribution policy; absent composition never grants access."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


OPERATIONS = ("set_pricing", "export_router_credentials", "can_manage_router", "can_use_router",
              "visible_routers", "validate_model_groups", "validate_pool_bindings", "validate_deployment", "candidate_outputs",
              "log_runtime_invocation")


def router_integration():
    try:
        backend = import_string(getattr(settings, "NEXUS_ROUTER_INTEGRATION", ""))()
    except (ImportError, AttributeError, ValueError, TypeError):
        raise ImproperlyConfigured("Router integration is not configured.") from None
    if not all(callable(getattr(backend, name, None)) for name in OPERATIONS):
        raise ImproperlyConfigured("Router integration is incomplete.")
    return backend
