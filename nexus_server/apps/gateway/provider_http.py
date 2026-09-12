from __future__ import annotations

import http.client
import ipaddress
import io
import socket
import ssl
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from django.conf import settings


class ProviderEndpointRejected(ValueError):
    """Raised before any bytes are sent to an unsafe Provider endpoint."""


@dataclass(frozen=True)
class ResolvedProviderEndpoint:
    scheme: str
    hostname: str
    port: int
    path: str
    addresses: tuple[tuple[int, int, int, tuple], ...]


def validate_provider_base_url(value: str, *, resolve: bool = False, allow_private: bool = False) -> str:
    parsed = urlsplit(str(value or "").strip())
    allow_http = bool(getattr(settings, "NEXUS_PROVIDER_ALLOW_HTTP", False))
    mock_hosts = set(getattr(settings, "NEXUS_GATEWAY_MOCK_HOSTS", set())) | set(
        getattr(settings, "NEXUS_GATEWAY_FAIL_HOSTS", set())
    )
    allow_http = allow_http or parsed.hostname in mock_hosts
    if parsed.scheme not in ({"https", "http"} if allow_http else {"https"}):
        raise ProviderEndpointRejected("Provider URL must use HTTPS.")
    if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ProviderEndpointRejected("Provider URL contains unsupported authority or fragment data.")
    if parsed.query:
        raise ProviderEndpointRejected("Provider base URL must not contain query parameters.")
    if resolve:
        resolve_provider_endpoint(value, allow_private=allow_private)
    return str(value).strip().rstrip("/")


def resolve_provider_endpoint(url: str, *, allow_private: bool = False,
                              public_https_only: bool = False) -> ResolvedProviderEndpoint:
    parsed = urlsplit(str(url or "").strip())
    allow_http = not public_https_only and bool(getattr(settings, "NEXUS_PROVIDER_ALLOW_HTTP", False))
    if public_https_only and any(ord(char) <= 32 or ord(char) == 127 for char in str(url)):
        raise ProviderEndpointRejected("Endpoint contains unsupported whitespace or control data.")
    if parsed.scheme not in ({"https", "http"} if allow_http else {"https"}):
        raise ProviderEndpointRejected("Provider endpoint must use HTTPS.")
    if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ProviderEndpointRejected("Provider endpoint contains unsupported authority or fragment data.")
    try:
        if parsed.port == 0:
            raise ValueError()
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ProviderEndpointRejected("Provider endpoint port is invalid.") from exc
    allowed_hosts = {
        item.strip().lower()
        for item in str(getattr(settings, "NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS", "")).split(",")
        if item.strip()
    }
    private_allowed = not public_https_only and (allow_private or parsed.hostname.lower() in allowed_hosts)
    try:
        rows = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ProviderEndpointRejected("Provider endpoint DNS resolution failed.") from exc
    if not rows:
        raise ProviderEndpointRejected("Provider endpoint DNS resolution returned no addresses.")
    addresses: list[tuple[int, int, int, tuple]] = []
    for family, socktype, proto, _canonical, address in rows:
        ip = ipaddress.ip_address(address[0])
        if not private_allowed and not ip.is_global:
            raise ProviderEndpointRejected("Provider endpoint must resolve exclusively to public addresses.")
        addresses.append((family, socktype, proto, address))
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"
    return ResolvedProviderEndpoint(
        scheme=parsed.scheme,
        hostname=parsed.hostname,
        port=port,
        path=path,
        addresses=tuple(addresses),
    )


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, endpoint: ResolvedProviderEndpoint, timeout: float):
        super().__init__(endpoint.hostname, endpoint.port, timeout=timeout)
        self.endpoint = endpoint

    def connect(self) -> None:
        self.sock = _connect_address(self.endpoint.addresses, self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, endpoint: ResolvedProviderEndpoint, timeout: float):
        super().__init__(endpoint.hostname, endpoint.port, timeout=timeout, context=ssl.create_default_context())
        self.endpoint = endpoint

    def connect(self) -> None:
        raw = _connect_address(self.endpoint.addresses, self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.endpoint.hostname)
        except Exception:
            raw.close()
            raise


def _connect_address(addresses: tuple[tuple[int, int, int, tuple], ...], timeout: float | None):
    last_error: OSError | None = None
    for family, socktype, proto, address in addresses:
        candidate = socket.socket(family, socktype, proto)
        candidate.settimeout(timeout)
        try:
            candidate.connect(address)
            return candidate
        except OSError as exc:
            candidate.close()
            last_error = exc
    raise URLError(last_error or "Provider endpoint connection failed.")


@contextmanager
def open_provider_url(
    url: str,
    *,
    method: str,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float,
    allow_private: bool = False,
    public_https_only: bool = False,
    error_body_limit: int = 64 * 1024,
):
    """Open one redirect-free, DNS-pinned Provider request."""
    endpoint = resolve_provider_endpoint(url, allow_private=allow_private, public_https_only=public_https_only)
    connection: http.client.HTTPConnection
    if endpoint.scheme == "https":
        connection = _PinnedHTTPSConnection(endpoint, timeout)
    else:
        connection = _PinnedHTTPConnection(endpoint, timeout)
    try:
        connection.request(method.upper(), endpoint.path, body=body, headers=headers or {})
        response = connection.getresponse()
        if response.status < 200 or response.status >= 300:
            encoded = response.read(error_body_limit + 1)[:error_body_limit]
            raise HTTPError(
                url,
                response.status,
                response.reason,
                response.headers,
                io.BytesIO(encoded),
            )
        yield response
    except (ProviderEndpointRejected, HTTPError, URLError, TimeoutError, OSError):
        raise
    finally:
        connection.close()
