"""Distribution-specific Provider relations and scope, without private models."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


OPERATIONS = ("runtime_prefetches", "runtime_filter", "offer_has_contributions", "remove_contributions", "validate_source_account",
              "connection_runtime_queryset", "connection_deletion_impact", "connection_removal_metadata",
              "maintenance_filter", "maintenance_summary", "validate_import_existing",
              "import_has_publication_dependencies", "import_failure_message")


def runtime_integration():
    try:
        backend = import_string(getattr(settings, "NEXUS_PROVIDER_RUNTIME_INTEGRATION", ""))()
    except (ImportError, AttributeError, TypeError, ValueError):
        raise ImproperlyConfigured("Provider runtime integration is not configured.") from None
    if not all(callable(getattr(backend, name, None)) for name in OPERATIONS):
        raise ImproperlyConfigured("Provider runtime integration is incomplete.")
    return backend
