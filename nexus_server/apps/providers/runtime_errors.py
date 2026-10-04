"""Shared Provider transport errors; release admission remains fail closed."""
from rest_framework import exceptions


class ProviderRuntimeUnavailable(exceptions.APIException):
    """Typed local transport loss; recovery observes state before retrying."""
    default_detail = "Provider Runtime Docker transport is unavailable; retry scheduled."
    default_code = "PROVIDER_RUNTIME_UNAVAILABLE"


def docker_daemon_transport_unavailable(result) -> bool:
    if result.returncode == 0:
        return False
    diagnostic = (getattr(result, "stderr", "") or getattr(result, "stdout", "") or "").lower()
    if any(marker in diagnostic for marker in (
        "permission denied", "access denied", "access is denied", "operation not permitted", "unauthorized", "forbidden",
    )):
        return False
    if any(marker in diagnostic for marker in (
        "cannot connect to the docker daemon", "is the docker daemon running?",
    )):
        return True
    return "error during connect:" in diagnostic and any(marker in diagnostic for marker in (
        "connection refused", "actively refused", "connection reset", "connectex:",
        "no such file or directory", "the system cannot find the file", "context deadline exceeded",
        "i/o timeout", "network is unreachable", "no route to host", "broken pipe",
    ))
