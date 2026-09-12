"""Resource admission contract, independent of commercial plans and wallets.

The personal edition supplies machine limits. The existing Cloud supplies its
commercial policy backend. Backend failure never means unlimited resources.
"""
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


@lru_cache(maxsize=16)
def _backend(path):
    if not path:
        raise ImproperlyConfigured("Nexus resource admission backend is not configured.")
    try:
        backend = import_string(path)()
    except (ImportError, AttributeError, TypeError) as exc:
        raise ImproperlyConfigured("Nexus resource admission backend could not be loaded.") from exc
    if not callable(getattr(backend, "enforce_resource", None)):
        raise ImproperlyConfigured("Nexus resource admission backend does not implement enforce_resource.")
    return backend


def enforce_tenant_resource_quota(*, tenant, resource: str) -> None:
    # Compatibility name for callers; the core no longer reads any Plan model.
    _backend(getattr(settings, "NEXUS_RESOURCE_ADMISSION_BACKEND", "")).enforce_resource(
        tenant=tenant, resource=resource,
    )


def _capacity_operation(name, **kwargs):
    backend = _backend(getattr(settings, "NEXUS_RESOURCE_ADMISSION_BACKEND", ""))
    operation = getattr(backend, name, None)
    if not callable(operation):
        raise ImproperlyConfigured("Nexus resource admission backend does not implement capacity reservations.")
    return operation(**kwargs)


def capability_state(*, tenant, code: str) -> dict:
    return _capacity_operation("capability_state", tenant=tenant, code=code)


def enforce_capability(*, tenant, code: str, requested=1) -> dict:
    return _capacity_operation("enforce_capability", tenant=tenant, code=code, requested=requested)


def reserve_capability(*, tenant, code: str, idempotency_key: str, amount=1, ttl_seconds=300):
    return _capacity_operation("reserve_capability", tenant=tenant, code=code,
        idempotency_key=idempotency_key, amount=amount, ttl_seconds=ttl_seconds)


def release_capability_reservation(*, tenant, code: str, idempotency_key: str) -> None:
    return _capacity_operation("release_capability_reservation", tenant=tenant, code=code,
        idempotency_key=idempotency_key)


def record_capability_usage(*, tenant, code: str, amount, idempotency_key: str, metadata=None):
    return _capacity_operation("record_capability_usage", tenant=tenant, code=code,
        amount=amount, idempotency_key=idempotency_key, metadata=metadata)
