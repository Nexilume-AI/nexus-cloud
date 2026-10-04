"""Short-lived coturn REST credentials. Call only after viewer/device authorization.

This module is shared by the personal and enterprise distributions. It never
returns the signing secret and does not enable any anonymous credential API.
"""
import base64
import hashlib
import hmac
from pathlib import Path
import re
import time
from uuid import UUID


class TurnConfigurationError(Exception):
    pass


def ice_configuration(settings, session_id, *, lifetime_seconds=1800, now=None):
    urls = getattr(settings, "NEXUS_MOBILE_TURN_URLS", "")
    if not urls:
        return {"ice_servers": [], "transport": "direct", "ice_transport_policy": "all"}
    urls = urls.split(",") if isinstance(urls, str) else urls
    if not isinstance(urls, list) or not 1 <= len(urls) <= 8 or any(
            not isinstance(u, str) or not re.fullmatch(
                r"turns?:[A-Za-z0-9.-]+:[0-9]{1,5}\?transport=(udp|tcp)", u) or
                not 1 <= int(u.split(":")[-1].split("?")[0]) <= 65535 for u in urls):
        raise TurnConfigurationError("MOBILE_TURN_URL_INVALID")
    if type(lifetime_seconds) is not int or not 60 <= lifetime_seconds <= 3600:
        raise TurnConfigurationError("MOBILE_TURN_LIFETIME_INVALID")
    try:
        path = Path(getattr(settings, "NEXUS_MOBILE_TURN_SECRET_FILE", ""))
        if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_size > 128:
            raise TurnConfigurationError("MOBILE_TURN_SECRET_UNAVAILABLE")
        secret = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError, TypeError):
        raise TurnConfigurationError("MOBILE_TURN_SECRET_UNAVAILABLE") from None
    if not re.fullmatch(r"[a-f0-9]{64}", secret):
        raise TurnConfigurationError("MOBILE_TURN_SECRET_INVALID")
    try:
        session = UUID(str(session_id)).hex
    except (ValueError, TypeError, AttributeError):
        raise TurnConfigurationError("MOBILE_TURN_SESSION_INVALID") from None
    username = f"{int(time.time() if now is None else now) + lifetime_seconds}:nexus-{session}"
    credential = base64.b64encode(hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()).decode()
    return {"ice_servers": [{"urls": urls, "username": username, "credential": credential}],
            # Both endpoints must use the same relay policy. Private/mDNS host
            # candidates cannot be reached across NAT and are intentionally not
            # accepted as TURN peers by the managed relay's SSRF protections.
            "transport": "relay", "ice_transport_policy": "relay", "expires_at": int(username.split(":")[0])}
