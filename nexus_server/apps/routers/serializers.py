"""Compatibility aliases for explicitly selected Router presentation."""
from .presentation import configured_serializer


def __getattr__(name):
    if name.startswith("_"):
        raise AttributeError(name)
    return configured_serializer(name)
