from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    project_id: str


_tenant_context: ContextVar[TenantContext] = ContextVar(
    "tenant_context",
    default=TenantContext(tenant_id="", project_id=""),
)


def set_tenant_context(tenant_id: str, project_id: str):
    return _tenant_context.set(TenantContext(tenant_id=tenant_id, project_id=project_id))


def reset_tenant_context(token) -> None:
    _tenant_context.reset(token)


def get_tenant_context() -> TenantContext:
    return _tenant_context.get()
