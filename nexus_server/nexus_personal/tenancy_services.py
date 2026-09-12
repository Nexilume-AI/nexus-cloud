"""Personal compatibility context, not Organization/Project administration."""
from apps.common.request_context import get_tenant_from_request

__all__ = ["get_tenant_from_request"]
