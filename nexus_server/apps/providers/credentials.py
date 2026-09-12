"""Distribution-selected Provider export credential issuer.

The Provider service never imports the commercial API Key model. A distribution
must supply a real issuer; an absent issuer must not return a placeholder key.
"""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


def issue_provider_export_key(*, request, runtime, model):
    path = getattr(settings, "NEXUS_PROVIDER_CREDENTIALS_BACKEND", "")
    try:
        backend = import_string(path)()
    except (ImportError, AttributeError, TypeError, ValueError):
        raise ImproperlyConfigured("Provider credential issuer is not configured.") from None
    issuer = getattr(backend, "issue_export_key", None)
    if not callable(issuer):
        raise ImproperlyConfigured("Provider credential issuer is incomplete.")
    return issuer(request=request, runtime=runtime, model=model)
