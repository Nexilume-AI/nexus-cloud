"""Invocation policy boundary. Core never manufactures a wallet or a free plan.

The operator-selected distribution supplies the complete lifecycle. Loading is
lazy to avoid importing commercial models during core module discovery. Missing
or incomplete implementations fail closed, including on release/lease renewal.
"""
from functools import lru_cache
from typing import Protocol, Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string
from rest_framework.exceptions import APIException


class InvocationFundingDenied(APIException):
    status_code = 400
    default_code = "INVOCATION_FUNDING_DENIED"
    default_detail = "Invocation funding was not authorized."


class InvocationLifecycle(Protocol):
    def pricing_snapshot(self, **kwargs) -> Any: ...
    def estimate(self, **kwargs) -> Any: ...
    def preflight(self, **kwargs) -> None: ...
    def begin(self, **kwargs) -> Any: ...
    def finalize(self, **kwargs) -> Any: ...
    def report(self, **kwargs) -> Any: ...
    def record(self, **kwargs) -> Any: ...
    def release_stale(self, **kwargs) -> int: ...
    def renew_lease(self, **kwargs) -> None: ...
    def capture_follow_up(self, **kwargs) -> dict: ...
    def recheck_follow_up(self, **kwargs) -> None: ...
    def verify_follow_up_reservation(self, **kwargs) -> None: ...


_METHODS = ("pricing_snapshot", "estimate", "preflight", "begin", "finalize", "report", "record", "release_stale", "renew_lease",
            "capture_follow_up", "recheck_follow_up", "verify_follow_up_reservation")


@lru_cache(maxsize=16)
def _backend(path: str) -> InvocationLifecycle:
    if not isinstance(path, str) or not path:
        raise ImproperlyConfigured("Nexus invocation lifecycle is not configured.")
    try:
        backend = import_string(path)()
    except (ImportError, AttributeError, TypeError) as exc:
        raise ImproperlyConfigured("Nexus invocation lifecycle could not be loaded.") from exc
    if any(not callable(getattr(backend, name, None)) for name in _METHODS):
        raise ImproperlyConfigured("Nexus invocation lifecycle is incomplete.")
    return backend


def _current() -> InvocationLifecycle:
    return _backend(getattr(settings, "NEXUS_INVOCATION_LIFECYCLE_BACKEND", ""))


def agent_pricing_snapshot(**kwargs):
    return _current().pricing_snapshot(**kwargs)


def calculate_agent_cost(**kwargs):
    return _current().estimate(**kwargs)


def invocation_preflight(**kwargs):
    return _current().preflight(**kwargs)


def begin_runtime_invocation(**kwargs):
    return _current().begin(**kwargs)


def finalize_runtime_invocation(**kwargs):
    return _current().finalize(**kwargs)


def report_invocation_billing(**kwargs):
    return _current().report(**kwargs)


def record_invocation(**kwargs):
    return _current().record(**kwargs)


def release_stale_invocation_reservations(**kwargs):
    return _current().release_stale(**kwargs)


def renew_invocation_lease(**kwargs):
    return _current().renew_lease(**kwargs)


def capture_follow_up_authority(**kwargs):
    value = _current().capture_follow_up(**kwargs)
    if not isinstance(value, dict):
        raise ImproperlyConfigured("Follow-up authority is invalid.")
    return value


def recheck_follow_up_authority(**kwargs):
    return _current().recheck_follow_up(**kwargs)


def verify_follow_up_reservation(**kwargs):
    return _current().verify_follow_up_reservation(**kwargs)
