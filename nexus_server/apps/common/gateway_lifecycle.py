"""Distribution-owned Gateway accounting; no implicit free or no-op backend.

The reservation object is a compatibility handle. Core passes it back unchanged
and never interprets its financial identifiers. Backend errors propagate without
retrying any upstream request. Image account locking must run inside the caller's
transaction, before the operation row, including after a worker restart.
"""
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from typing import Any, Protocol
import uuid

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


@dataclass(frozen=True)
class GatewayChargeReservation:
    ledger_entry_id: int | None
    credit_hold_id: uuid.UUID | None
    request_id: str
    amount: Decimal


class GatewayLifecycle(Protocol):
    def image_price(self, **kwargs) -> Any: ...
    def estimate(self, **kwargs) -> Any: ...
    def calculate(self, **kwargs) -> Any: ...
    def reserve(self, **kwargs) -> Any: ...
    def release(self, **kwargs) -> Any: ...
    def finalize(self, **kwargs) -> Any: ...
    def record(self, **kwargs) -> Any: ...
    def lock_image_account(self, **kwargs) -> Any: ...
    def expire_images(self, **kwargs) -> Any: ...


_METHODS = ("estimate", "calculate", "reserve", "release", "finalize", "record", "lock_image_account", "expire_images", "image_price")


@lru_cache(maxsize=16)
def _backend(path: str) -> GatewayLifecycle:
    if not isinstance(path, str) or not path:
        raise ImproperlyConfigured("Nexus Gateway lifecycle is not configured.")
    try:
        backend = import_string(path)()
    except (ImportError, AttributeError, TypeError) as exc:
        raise ImproperlyConfigured("Nexus Gateway lifecycle could not be loaded.") from exc
    if any(not callable(getattr(backend, name, None)) for name in _METHODS):
        raise ImproperlyConfigured("Nexus Gateway lifecycle is incomplete.")
    return backend


def _current() -> GatewayLifecycle:
    return _backend(getattr(settings, "NEXUS_GATEWAY_LIFECYCLE_BACKEND", ""))


def estimate_gateway_charge(**kwargs):
    return _current().estimate(**kwargs)


def image_price(deployment, operation, payload):
    return _current().image_price(deployment=deployment, operation=operation, payload=payload)


def calculate_cost(**kwargs):
    return _current().calculate(**kwargs)


def reserve_gateway_charge(**kwargs):
    return _current().reserve(**kwargs)


def release_gateway_charge_reservation(**kwargs):
    return _current().release(**kwargs)


def finalize_gateway_success(**kwargs):
    return _current().finalize(**kwargs)


def apply_success_side_effects(**kwargs):
    return _current().record(**kwargs)


def lock_image_account(**kwargs):
    return _current().lock_image_account(**kwargs)


def expire_image_operations(**kwargs):
    return _current().expire_images(**kwargs)
