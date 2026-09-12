"""Required distribution policy for Source origin, scope and optional publication."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


OPERATIONS = ("create_additional_source", "create_deployment_from_marketplace", "resolve_marketplace_model_group", "marketplace_source_id",
              "visible_deployments", "visible_model_groups", "resolve_request_project",
              "scope_queryset_to_request_ownership", "batch_source_data", "remove_contributions",
              "validate_model_group", "validate_restoration", "validate_routing_preview", "affected_router_ids", "topology")


def deployment_integration():
    try:
        backend = import_string(getattr(settings, "NEXUS_DEPLOYMENT_INTEGRATION", ""))()
    except (ImportError, AttributeError, ValueError, TypeError):
        raise ImproperlyConfigured("Source integration is not configured.") from None
    if not all(callable(getattr(backend, name, None)) for name in OPERATIONS):
        raise ImproperlyConfigured("Source integration is incomplete.")
    return backend
