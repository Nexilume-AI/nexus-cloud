"""Required runtime access policy; no inferred edition or public fallback."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

OPERATIONS = ("runtime_actor_user", "get_runtime_use_agent", "enforce_api_key_agent_policy", "restore_request")


def invoke_runtime_policy(operation, **kwargs):
    if operation not in OPERATIONS:
        raise ImproperlyConfigured("Unknown Agent runtime policy operation.")
    path = getattr(settings, "NEXUS_AGENT_RUNTIME_POLICY_BACKEND", "")
    try:
        backend = import_string(path)()
    except (ImportError, AttributeError, TypeError, ValueError):
        raise ImproperlyConfigured("Agent runtime access policy is not configured.") from None
    if not all(callable(getattr(backend, name, None)) for name in OPERATIONS):
        raise ImproperlyConfigured("Agent runtime access policy is incomplete.")
    return getattr(backend, operation)(**kwargs)
