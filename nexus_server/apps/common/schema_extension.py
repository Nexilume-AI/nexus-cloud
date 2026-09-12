"""Explicit host schema composition, preserving existing tables and indexes."""
from importlib import import_module
from django.conf import settings
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured


def schema_extension(setting_name):
    path = getattr(settings, setting_name, "")
    if not isinstance(path, str) or not path:
        raise ImproperlyConfigured("Model extension must be explicitly configured.")
    try:
        module = import_module(path)
    except (ImportError, AttributeError, ValueError):
        raise ImproperlyConfigured("Model extension is unavailable.") from None
    if not callable(getattr(module, "register_models", None)):
        raise ImproperlyConfigured("Model extension is incomplete.")
    return module


def add_field(model, name, expected):
    try:
        existing = model._meta.get_field(name)
    except FieldDoesNotExist:
        model.add_to_class(name, expected)
    else:
        if existing.deconstruct()[1:] != expected.deconstruct()[1:]:
            raise ImproperlyConfigured("Schema field conflicts with its distribution.")


def add_index(model, expected):
    matching = [index for index in model._meta.indexes if index.name == expected.name]
    if matching:
        if len(matching) != 1 or matching[0].deconstruct() != expected.deconstruct():
            raise ImproperlyConfigured("Schema index conflicts with its distribution.")
    else:
        model._meta.indexes.append(expected)
        model._meta.original_attrs["indexes"] = list(model._meta.indexes)
