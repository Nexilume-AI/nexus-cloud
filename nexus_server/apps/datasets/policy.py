"""Explicit Dataset edition policy. Core storage never imports private billing."""
from functools import lru_cache
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


OPERATIONS = (
    "ensure_operational_alerts", "maintain_data_assets",
    "dataset_retention_status", "validate_dataset_file_publication",
    "dataset_related_fields",
    "search_public_datasets", "list_marketplace_datasets", "get_marketplace_dataset",
    "get_marketplace_dataset_version", "pull_marketplace_dataset", "acquisition_queryset",
    "list_dataset_acquisitions", "get_dataset_acquisition", "dataset_acquisition_payload",
    "set_pricing", "effective_dataset_pricing", "calculate_dataset_pull_cost", "dataset_pull_currency",
    "download_marketplace_dataset_file", "download_dataset_acquisition_file",
    "has_marketplace_dataset_entitlement", "validate_dataset_publication", "set_visibility",
    "visible_datasets", "can_read_dataset", "can_manage_dataset", "require_dataset_admin",
)


@lru_cache(maxsize=16)
def _backend(path):
    try:
        backend = import_string(path)()
    except (ImportError, AttributeError, TypeError, ValueError):
        raise ImproperlyConfigured("Nexus Dataset policy backend is not configured correctly.") from None
    if not all(callable(getattr(backend, operation, None)) for operation in OPERATIONS):
        raise ImproperlyConfigured("Nexus Dataset policy backend is incomplete.")
    return backend


def invoke_dataset_policy(operation, **kwargs):
    if operation not in OPERATIONS:
        raise ImproperlyConfigured("Unknown Dataset policy operation.")
    backend = _backend(getattr(settings, "NEXUS_DATASET_POLICY_BACKEND", ""))
    return getattr(backend, operation)(**kwargs)
