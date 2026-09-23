"""Build the public CA policy at the trusted Cloud deployment boundary."""
import hashlib
import json
import re
import ssl
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from rest_framework.exceptions import ValidationError


def cloud_trust_fingerprint(policy):
    """Digest the actual deployment policy, never persist its PEM in lifecycle state."""
    return hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def hosted_cloud_trust():
    ca_file = str(getattr(settings, "NEXUS_AGENT_RUNTIME_CLOUD_CA_FILE", "") or "")
    origins = []
    try:
        for name in ("NEXUS_PUBLIC_BASE_URL", "NEXUS_AGENT_DISPLAY_EVENTS_BASE_URL", "NEXUS_AGENT_WORKSPACE_API_BASE_URL"):
            value = str(getattr(settings, name, "") or "")
            if not value:
                continue
            parsed = urlsplit(value)
            if parsed.scheme != "https":
                if ca_file:
                    raise ValueError()
                # Preserve explicit local HTTP development without private CA.
                return {}
            if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
                raise ValueError()
            host = parsed.hostname.encode("idna").decode("ascii").lower()
            host = f"[{host}]" if ":" in host else host
            if parsed.port not in (None, 443):
                host += f":{parsed.port}"
            origin = urlunsplit(("https", host, "", "", ""))
            if origin not in origins:
                origins.append(origin)
        if not origins:
            if ca_file:
                raise ValueError()
            return {}
        policy = {"schema_version": 1, "mode": "system", "origins": origins}
        if ca_file:
            path = Path(ca_file)
            if not path.is_file():
                raise ValueError()
            with path.open("rb") as stream:
                data = stream.read(16385)
            # Keep the deployment environment portable to Windows. Never ship the
            # system bundle, a private key, or unbounded configuration contents.
            pem = data.decode("ascii")
            if len(data) > 16384 or not re.fullmatch(r"(?:\s*-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\r\n]+-----END CERTIFICATE-----\s*)+", pem):
                raise ValueError()
            ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cadata=pem)
            policy.update(mode="pinned-pem", ca_pem=pem, sha256=hashlib.sha256(data).hexdigest())
        return policy
    except (OSError, ValueError, UnicodeError):
        raise ValidationError({"code": "RUN_CONTEXT_TRUST_UNAVAILABLE", "message": "Managed Agent Cloud trust is invalid. Check HTTPS origins and the public CA certificate bundle."}) from None
