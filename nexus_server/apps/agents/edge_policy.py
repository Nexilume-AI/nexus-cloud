"""Host-selected Edge ownership and failure policy, not a device input."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


def edge_policy():
    try:
        backend = import_string(getattr(settings, 'NEXUS_EDGE_POLICY_BACKEND', ''))()
    except (ImportError, AttributeError, TypeError, ValueError):
        raise ImproperlyConfigured('Edge policy backend is not configured.') from None
    if not all(callable(getattr(backend, name, None)) for name in (
        'managed_agent_owner', 'resolve_router_project', 'registration_failure', 'validate_node', 'validate_pairing', 'node_context', 'can_manage_relay')):
        raise ImproperlyConfigured('Edge policy backend is incomplete.')
    return backend
