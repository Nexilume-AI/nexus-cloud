"""Model-free distribution guard, safe before Django populates its registry."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def require_personal_distribution():
    if getattr(settings, "NEXUS_DISTRIBUTION", "") != "community" or getattr(
        settings, "NEXUS_IDENTITY_BACKEND", ""
    ) != "nexus_personal.identity.DatabasePersonalIdentityBackend":
        raise ImproperlyConfigured("Personal setup requires the explicit Community distribution.")
