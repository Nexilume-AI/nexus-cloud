"""Mobile pairing addresses come from deployment configuration, never a request host."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.core.validators import URLValidator
from django.core.exceptions import ValidationError


def mobile_pairing_connection() -> dict:
    configured = (
        getattr(settings, "NEXUS_MOBILE_PUBLIC_BASE_URL", "")
        or getattr(settings, "NEXUS_PUBLIC_BASE_URL", "")
    )
    try:
        if not isinstance(configured, str) or configured != configured.strip():
            raise ValueError
        if "\\" in configured or any(char.isspace() for char in configured):
            raise ValueError
        URLValidator(schemes=["https"])(configured)
        url = urlsplit(configured)
        host = (url.hostname or "").lower().rstrip(".")
        if url.username is not None or url.password is not None or url.query or url.fragment:
            raise ValueError
        if url.path not in ("", "/") or not host or host == "localhost" or host.endswith(".localhost"):
            raise ValueError
        if host == "10.0.2.2" or "%" in host:
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None:
            address = getattr(address, "ipv4_mapped", None) or address
            if address.is_loopback or address.is_unspecified or address.is_link_local or address.is_multicast:
                raise ValueError
        port = url.port
        if port is not None and port < 1:
            raise ValueError
        authority = f"[{host}]" if ":" in host else host.encode("idna").decode("ascii")
        if port not in (None, 443):
            authority += f":{port}"
        return {
            "pairing_base_url": urlunsplit(("https", authority, "", "", "")),
            "pairing_error": None,
        }
    except (ValueError, ValidationError, UnicodeError):
        # Do not echo a potentially misconfigured URL containing credentials.
        return {
            "pairing_base_url": None,
            "pairing_error": {
                "code": "MOBILE_PAIRING_ADDRESS_UNAVAILABLE",
                "message": "Ask the administrator to configure a phone-accessible HTTPS Cloud address, then generate a new pairing QR.",
            },
        }
