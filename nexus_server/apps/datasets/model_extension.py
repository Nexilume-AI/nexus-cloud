"""Dataset model additions are host configuration, never a runtime feature flag."""
from importlib import import_module
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def model_extension():
    path = getattr(settings, "NEXUS_DATASET_MODEL_EXTENSION", "")
    if not isinstance(path, str) or not path:
        raise ImproperlyConfigured("Dataset model extension is not configured.")
    try:
        extension = import_module(path)
    except ImportError:
        raise ImproperlyConfigured("Dataset model extension could not be loaded.") from None
    if not callable(getattr(extension, "register_models", None)):
        raise ImproperlyConfigured("Dataset model extension is incomplete.")
    return extension
