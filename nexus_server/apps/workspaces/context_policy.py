"""Host-owned Computer context, supplemental to signed Runtime authentication."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


def _backend():
    path = getattr(settings, "NEXUS_WORKSPACE_CONTEXT_BACKEND", "")
    if not isinstance(path, str) or not path:
        raise ImproperlyConfigured("Workspace context policy is not configured.")
    try:
        backend = import_string(path)()
    except (ImportError, AttributeError, TypeError):
        raise ImproperlyConfigured("Workspace context policy could not be loaded.") from None
    if not all(callable(getattr(backend, name, None)) for name in (
        "resolve_project", "connection_valid", "visible_connections",
        "visible_terminals", "terminal_valid", "terminal_identity",
    )):
        raise ImproperlyConfigured("Workspace context policy is incomplete.")
    return backend


def pairing_project(*, request, tenant, project_id):
    return _backend().resolve_project(request=request, tenant=tenant, project_id=project_id)


def connection_context_valid(connection):
    return _backend().connection_valid(connection) is True


def scope_connections(*, request, queryset):
    return _backend().visible_connections(request=request, queryset=queryset)


def scope_terminals(*, request, queryset):
    return _backend().visible_terminals(request=request, queryset=queryset)


def terminal_context_valid(session):
    return _backend().terminal_valid(session) is True


def validate_terminal_identity(*, scope, user, token):
    return _backend().terminal_identity(scope=scope, user=user, token=token)
