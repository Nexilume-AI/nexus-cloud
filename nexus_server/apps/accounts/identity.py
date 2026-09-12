"""Explicit distribution identity lifecycle; never infer a permissive edition.

Core verifies provider responses and passwords. The host decides whether an
identity may enter and how its context is provisioned. Device delegate tokens
and resource authorization remain separate safety boundaries.
"""
from functools import lru_cache
from typing import Protocol

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


class ExternalIdentityError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class IdentityBackend(Protocol):
    def password_context(self, *, request, user) -> tuple[str, str]: ...
    def authenticate_external_user(self, **kwargs): ...
    def authenticate_machine_token(self, *, request, token: str): ...
    def authenticate_api_key(self, *, request, token: str): ...


@lru_cache(maxsize=16)
def _backend(path: str) -> IdentityBackend:
    if not path:
        raise ImproperlyConfigured("Nexus identity backend is not configured.")
    try:
        backend = import_string(path)()
    except (ImportError, AttributeError, TypeError):
        raise ImproperlyConfigured("Nexus identity backend could not be loaded.") from None
    if not all(callable(getattr(backend, method, None)) for method in (
        "password_context", "authenticate_external_user", "authenticate_machine_token",
        "authenticate_api_key",
    )):
        raise ImproperlyConfigured("Nexus identity backend is incomplete.")
    return backend


def identity_backend() -> IdentityBackend:
    return _backend(getattr(settings, "NEXUS_IDENTITY_BACKEND", ""))


def password_context(*, request, user) -> tuple[str, str]:
    result = identity_backend().password_context(request=request, user=user)
    if not isinstance(result, tuple) or len(result) != 2 or not all(isinstance(value, str) for value in result):
        raise ImproperlyConfigured("Nexus identity backend returned an invalid context.")
    return result


def authenticate_external_user(**kwargs):
    return identity_backend().authenticate_external_user(**kwargs)


def authenticate_machine_token(*, request, token: str):
    return identity_backend().authenticate_machine_token(request=request, token=token)


def authenticate_api_key(*, request, token: str):
    return identity_backend().authenticate_api_key(request=request, token=token)
