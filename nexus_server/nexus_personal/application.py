"""Personal ASGI application factory, with no implicit distribution fallback.

The operator must supply the completed personal settings explicitly. This is
not a settings module and does not claim the remaining product composition is
release-ready. Computer Runtime and Terminal use their original ASGI handlers.
"""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def create_application():
    from .configuration import require_personal_distribution

    require_personal_distribution()
    if settings.ROOT_URLCONF != "nexus_personal.urls":
        raise ImproperlyConfigured("Personal ASGI requires the personal product URL configuration.")
    if getattr(settings, "NEXUS_AUTHORIZATION_BACKEND", "") != "nexus_personal.authorization.PersonalAuthorizationBackend":
        raise ImproperlyConfigured("Personal ASGI requires its explicit owner authorization backend.")

    from django.core.asgi import get_asgi_application
    django_app = get_asgi_application()
    from apps.workspaces.asgi import WorkspaceASGIProxy

    # No SSH reaper or Cloud/Agent-host Browser is started. Runtime Browser
    # sessions and their exact local process/profile lifecycle belong to the
    # paired Computer. HTTP and WebSocket authorization remain in the handlers.
    return WorkspaceASGIProxy(django_app)
