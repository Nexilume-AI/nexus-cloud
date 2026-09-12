from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import socket
import ssl
import struct
import time
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.conf import settings
from django.utils import timezone
from rest_framework import exceptions, status

from .edge_services import authenticate_edge_node, presence_sweeper_status
from .models import EdgeNode


class RelayNotConfigured(exceptions.APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Nexus Cloud Relay is not configured. Use Direct IPv6 or ask the Cloud administrator to enable Relay."
    default_code = "RELAY_NOT_CONFIGURED"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def relay_endpoints() -> dict[str, dict[str, Any]]:
    configured = getattr(settings, "NEXUS_RELAY_ENDPOINTS", {}) or {}
    if not isinstance(configured, dict):
        raise exceptions.APIException("Nexus Relay endpoint configuration is invalid.")
    return configured


def _relay_config(relay_id: str) -> dict[str, Any]:
    config = relay_endpoints().get(relay_id)
    if not isinstance(config, dict):
        raise exceptions.ValidationError({"relay_id": ["Relay is not trusted by Nexus Server."]})
    required = {"router_id", "domain_id", "assignment_endpoint", "invoke_endpoint"}
    if any(not str(config.get(name) or "") for name in required):
        raise exceptions.APIException("Trusted Relay configuration is incomplete.")
    return config


def _ticket_keys() -> dict[str, bytes]:
    values = getattr(settings, "NEXUS_RELAY_TICKET_KEYS", {}) or {}
    keys: dict[str, bytes] = {}
    for key_id, encoded in values.items():
        try:
            secret = base64.urlsafe_b64decode(str(encoded) + "=" * (-len(str(encoded)) % 4))
        except (ValueError, TypeError) as exc:
            raise exceptions.APIException("Relay ticket key is invalid.") from exc
        if not 32 <= len(secret) <= 64:
            raise exceptions.APIException("Relay ticket keys must contain 32 to 64 bytes.")
        keys[str(key_id)] = secret
    return keys


def issue_relay_ticket(*, node: EdgeNode, relay_id: str, assignment_id: str, now: int | None = None) -> tuple[str, int]:
    now = int(time.time()) if now is None else int(now)
    ttl = max(30, min(int(getattr(settings, "NEXUS_RELAY_TICKET_TTL_SECONDS", 120)), 300))
    key_id = str(getattr(settings, "NEXUS_RELAY_TICKET_ACTIVE_KEY_ID", "relay-ticket-1"))
    secret = _ticket_keys().get(key_id)
    if secret is None:
        raise exceptions.APIException("Active Relay ticket signing key is unavailable.")
    claims = {
        "v": 1,
        "aid": assignment_id,
        "relay": relay_id,
        "router": node.router_id,
        "domain": node.domain_id,
        "san": f"{node.router_id}.{node.domain_id}",
        "iat": now,
        "exp": now + ttl,
        "nonce": secrets.token_hex(16),
    }
    payload = _b64(json.dumps(claims, separators=(",", ":"), ensure_ascii=True).encode("ascii"))
    signing_input = f"nrt1.{key_id}.{payload}"
    signature = _b64(hmac.new(secret, signing_input.encode("ascii"), hashlib.sha256).digest())
    return f"{signing_input}.{signature}", ttl


def assign_relay(*, request, data: dict[str, Any]) -> dict[str, Any]:
    node = authenticate_edge_node(request)
    require_relay_configuration()
    if data["router_id"] != node.router_id or data["domain_id"].lower() != node.domain_id:
        raise exceptions.AuthenticationFailed("Relay assignment identity does not match the enrolled device.")
    endpoints = relay_endpoints()
    if not endpoints:
        raise exceptions.APIException("No Nexus Relay is configured.")
    current = str(data.get("current_relay_id") or "")
    failed = str(data.get("failed_relay_id") or "")
    ordered = list(endpoints)
    relay_id = current if current in endpoints and current != failed else next(
        (candidate for candidate in ordered if candidate != failed), ""
    )
    # A failed Relay hint is advisory: it lets the Directory prefer another
    # trusted Relay, but it must not permanently strand a deployment that has
    # only one configured Relay. Reissuing a short-lived ticket for that same
    # trusted endpoint gives the tunnel a bounded recovery path.
    if not relay_id and len(ordered) == 1:
        relay_id = ordered[0]
    if not relay_id:
        raise exceptions.APIException("No healthy Nexus Relay is available.")
    config = _relay_config(relay_id)
    assignment_id = f"edge-{str(node.id).replace('-', '')[:32]}"
    ticket, ticket_ttl = issue_relay_ticket(node=node, relay_id=relay_id, assignment_id=assignment_id)
    lease_seconds = max(ticket_ttl, min(int(config.get("lease_seconds") or 300), 3600))
    node.connectivity_mode = EdgeNode.CONNECTIVITY_RELAY
    node.relay_id = relay_id
    node.relay_assignment_id = assignment_id
    node.relay_lease_expires_at = timezone.now() + timedelta(seconds=lease_seconds)
    node.save(update_fields=[
        "connectivity_mode", "relay_id", "relay_assignment_id",
        "relay_lease_expires_at", "updated_at",
    ])
    return {
        "version": 1,
        "assignment_id": assignment_id,
        "relay_id": relay_id,
        "relay_router_id": str(config["router_id"]),
        "relay_domain_id": str(config["domain_id"]),
        "relay_endpoint": str(config["assignment_endpoint"]),
        "connect_ipv4": str(config.get("connect_ipv4") or ""),
        "session_ticket": ticket,
        "lease_seconds": lease_seconds,
    }


def _private_rsa(filename: str, purpose: str) -> rsa.RSAPrivateKey:
    if not filename:
        raise exceptions.APIException(f"{purpose} signing key is not configured.")
    try:
        key = serialization.load_pem_private_key(Path(filename).read_bytes(), password=None)
    except (OSError, ValueError, TypeError) as exc:
        raise exceptions.APIException(f"{purpose} signing key could not be loaded.") from exc
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise exceptions.APIException(f"{purpose} signing key must be an RSA key of at least 2048 bits.")
    return key


def forwarding_trust_descriptor() -> dict[str, str]:
    filename = str(getattr(settings, "NEXUS_RELAY_FORWARDING_PRIVATE_KEY_FILE", "") or "")
    if not filename:
        return {}
    key = _private_rsa(filename, "Relay forwarding")
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return {
        "public_key": public_pem,
        "key_id": str(getattr(settings, "NEXUS_RELAY_FORWARDING_KEY_ID", "nexus-cloud-1")),
        "issuer": str(getattr(settings, "NEXUS_RELAY_FORWARDING_ISSUER", "nexus-cloud")),
        "source_router_id": str(getattr(settings, "NEXUS_RELAY_SOURCE_ROUTER_ID", "nexus-cloud")),
    }


def relay_device_trust_descriptor() -> dict[str, Any]:
    endpoints = relay_endpoints()
    if not endpoints:
        return {}
    relay_ids: list[str] = []
    certificates: list[str] = []
    for relay_id in endpoints:
        config = _relay_config(relay_id)
        ca_file = str(config.get("router_ca_file") or "")
        if not ca_file:
            raise exceptions.APIException(f"Relay device CA is not configured for {relay_id}.")
        try:
            ca_certificate = Path(ca_file).read_text(encoding="ascii").strip()
        except (OSError, UnicodeError) as exc:
            raise exceptions.APIException("Relay device CA bundle could not be loaded.") from exc
        if "-----BEGIN CERTIFICATE-----" not in ca_certificate or "PRIVATE KEY" in ca_certificate:
            raise exceptions.APIException("Relay device CA bundle is invalid.")
        relay_ids.append(relay_id)
        if ca_certificate not in certificates:
            certificates.append(ca_certificate)
    bundle = "\n".join(certificates) + "\n"
    if len(bundle.encode("ascii")) > 65536:
        raise exceptions.APIException("Relay device CA bundle exceeds the configured bound.")
    return {
        "relay_id": relay_ids[0] if len(relay_ids) == 1 else "",
        "relay_ids": relay_ids,
        "ca_certificate": bundle,
    }


def _validate_static_relay_configuration() -> None:
    """Validate credentials and trust without considering the operator switch."""

    endpoints = relay_endpoints()
    if not endpoints:
        raise RelayNotConfigured()
    try:
        for relay_id in endpoints:
            _relay_config(relay_id)

        ticket_keys = _ticket_keys()
        active_ticket_key = str(
            getattr(settings, "NEXUS_RELAY_TICKET_ACTIVE_KEY_ID", "relay-ticket-1")
        )
        if active_ticket_key not in ticket_keys:
            raise RelayNotConfigured()

        relay_ca = str(getattr(settings, "NEXUS_RELAY_CA_FILE", "") or "")
        relay_cert = str(getattr(settings, "NEXUS_RELAY_CLIENT_CERT_FILE", "") or "")
        relay_key = str(getattr(settings, "NEXUS_RELAY_CLIENT_KEY_FILE", "") or "")
        if not all(Path(filename).is_file() for filename in (relay_ca, relay_cert, relay_key)):
            raise RelayNotConfigured()
        tls_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=relay_ca)
        tls_context.load_cert_chain(certfile=relay_cert, keyfile=relay_key)

        _private_rsa(
            str(getattr(settings, "NEXUS_RELAY_JWT_PRIVATE_KEY_FILE", "") or ""),
            "Relay JWT",
        )
        forwarding_trust_descriptor()
        relay_device_trust_descriptor()
    except RelayNotConfigured:
        raise
    except (exceptions.APIException, OSError, ssl.SSLError, ValueError, TypeError, AttributeError) as exc:
        raise RelayNotConfigured() from exc


def _relay_control_file() -> Path | None:
    filename = str(getattr(settings, "NEXUS_RELAY_CONTROL_FILE", "") or "").strip()
    return Path(filename) if filename else None


def relay_control_enabled() -> bool:
    """Read the live operator switch; legacy deployments remain enabled."""

    filename = _relay_control_file()
    if filename is None:
        return True
    try:
        document = json.loads(filename.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    return document.get("enabled") is True


def set_relay_control_enabled(enabled: bool) -> None:
    filename = _relay_control_file()
    if filename is None:
        raise exceptions.APIException("Nexus Relay operator control is not configured.")
    filename.parent.mkdir(parents=True, exist_ok=True)
    temporary = filename.with_name(f".{filename.name}.{secrets.token_hex(6)}.tmp")
    document = {
        "version": 1,
        "enabled": bool(enabled),
        "updated_at": timezone.now().isoformat(),
    }
    try:
        temporary.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
        temporary.replace(filename)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise exceptions.APIException("Nexus Relay operator state could not be saved.") from exc


def _endpoint_descriptor(value: str, *, scope: str) -> dict[str, Any]:
    parsed = urlparse(value)
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        port = 0
    return {
        "scheme": parsed.scheme,
        "host": parsed.hostname or "",
        "port": port,
        "path": parsed.path or "/",
        "scope": scope,
    }


def _endpoint_accepting_connections(value: str) -> bool:
    descriptor = _endpoint_descriptor(value, scope="")
    if not descriptor["host"] or not descriptor["port"]:
        return False
    timeout = max(
        0.05,
        min(float(getattr(settings, "NEXUS_RELAY_RUNTIME_PROBE_TIMEOUT_SECONDS", 0.35)), 2.0),
    )
    try:
        with socket.create_connection((descriptor["host"], descriptor["port"]), timeout=timeout):
            return True
    except (OSError, TimeoutError):
        return False


def relay_runtime_status() -> dict[str, Any]:
    endpoints = relay_endpoints()
    relay_id = next(iter(endpoints), "")
    config = endpoints.get(relay_id) if relay_id else None
    if not isinstance(config, dict):
        return {
            "relay_id": "",
            "running": False,
            "router_endpoint": None,
            "cloud_invoke_endpoint": None,
        }
    router_endpoint = _endpoint_descriptor(str(config.get("assignment_endpoint") or ""), scope="router")
    cloud_endpoint = _endpoint_descriptor(str(config.get("invoke_endpoint") or ""), scope="cloud_internal")
    probe_enabled = bool(getattr(settings, "NEXUS_RELAY_RUNTIME_PROBE", False))
    router_healthy = not probe_enabled or _endpoint_accepting_connections(str(config.get("assignment_endpoint") or ""))
    cloud_healthy = not probe_enabled or _endpoint_accepting_connections(str(config.get("invoke_endpoint") or ""))
    return {
        "relay_id": relay_id,
        "running": router_healthy and cloud_healthy,
        "router_listener_healthy": router_healthy,
        "cloud_listener_healthy": cloud_healthy,
        "router_endpoint": router_endpoint,
        "cloud_invoke_endpoint": cloud_endpoint,
    }


def relay_service_status(*, can_manage: bool = False) -> dict[str, Any]:
    configured = True
    try:
        _validate_static_relay_configuration()
    except RelayNotConfigured:
        configured = False
    runtime = relay_runtime_status()
    enabled = relay_control_enabled()
    running = configured and bool(runtime.get("running"))
    available = configured and enabled and running
    if not configured:
        reason = "configuration_incomplete"
    elif not running:
        reason = "relay_process_unavailable"
    elif not enabled:
        reason = "operator_disabled"
    else:
        reason = "ready"
    return {
        **runtime,
        "presence_sweeper": presence_sweeper_status(),
        "configured": configured,
        "running": running,
        "enabled": enabled,
        "available": available,
        "reason": reason,
        "can_manage": bool(can_manage),
    }


def require_relay_configuration() -> None:
    """Validate the complete, enabled Cloud Relay before advertising it."""

    _validate_static_relay_configuration()
    if not relay_control_enabled():
        raise RelayNotConfigured()
    if not relay_runtime_status().get("running"):
        raise RelayNotConfigured()


def relay_configuration_available() -> bool:
    try:
        require_relay_configuration()
    except RelayNotConfigured:
        return False
    return True


def issue_relay_access_token(*, relay_id: str, target_router_id: str, target_agent: str, intent: str, task_id: str, body: bytes) -> str:
    now = int(time.time())
    ttl = max(15, min(int(getattr(settings, "NEXUS_RELAY_JWT_TTL_SECONDS", 60)), 120))
    header = {
        "alg": "RS256",
        "kid": str(getattr(settings, "NEXUS_RELAY_JWT_KEY_ID", "relay-rs256-1")),
        "typ": "at+jwt",
    }
    payload = {
        "iss": str(getattr(settings, "NEXUS_RELAY_JWT_ISSUER", "https://nexus.local/relay")),
        "aud": f"urn:nexus:relay:{relay_id}",
        "sub": "service://nexus-server",
        "scope": "relay.invoke",
        "target_router": target_router_id,
        "target_agent": target_agent,
        "intent": intent,
        "task_id": task_id,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
        "jti": secrets.token_hex(16),
    }
    head = _b64(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    claims = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{head}.{claims}".encode("ascii")
    key = _private_rsa(str(getattr(settings, "NEXUS_RELAY_JWT_PRIVATE_KEY_FILE", "")), "Relay JWT")
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{claims}.{_b64(signature)}"


def _put_text(value: str, maximum: int) -> bytes:
    encoded = value.encode("ascii")
    if not encoded or len(encoded) >= maximum:
        raise exceptions.APIException("Forwarding assertion field exceeds its protocol bound.")
    return struct.pack(">H", len(encoded)) + encoded


def issue_forwarding_assertion(*, source_router_id: str, target_router_id: str, source_agent: str, tenant: str, intent: str, task_id: str, hop_limit: int, body: bytes) -> str:
    now = int(time.time())
    ttl = max(5, min(int(getattr(settings, "NEXUS_RELAY_FORWARDING_TTL_SECONDS", 30)), 300))
    fixed = bytearray(68)
    fixed[0] = 1
    fixed[1] = hop_limit
    fixed[4:12] = struct.pack(">Q", now)
    fixed[12:20] = struct.pack(">Q", now + ttl)
    fixed[20:36] = secrets.token_bytes(16)
    fixed[36:68] = hashlib.sha256(body).digest()
    issuer = str(getattr(settings, "NEXUS_RELAY_FORWARDING_ISSUER", "nexus-cloud"))
    payload = bytes(fixed) + b"".join([
        _put_text(issuer, 128),
        _put_text(source_router_id, 65),
        _put_text(target_router_id, 65),
        _put_text(source_agent, 256),
        _put_text(tenant, 64),
        _put_text(intent, 128),
        _put_text(task_id, 65),
    ])
    key_id = str(getattr(settings, "NEXUS_RELAY_FORWARDING_KEY_ID", "nexus-cloud-1"))
    signing_text = f"nfa1.{key_id}.{_b64(payload)}"
    key = _private_rsa(str(getattr(settings, "NEXUS_RELAY_FORWARDING_PRIVATE_KEY_FILE", "")), "Relay forwarding")
    signature = key.sign(signing_text.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_text}.{_b64(signature)}"


def relay_invoke(*, relay_id: str, target_router_id: str, target_agent: str, tenant: str, intent: str, task_id: str, body: bytes, timeout: float) -> tuple[int, dict[str, str], bytes]:
    config = _relay_config(relay_id)
    source_router = str(getattr(settings, "NEXUS_RELAY_SOURCE_ROUTER_ID", "nexus-cloud"))
    source_agent = "service://nexus-server"
    nfa = issue_forwarding_assertion(
        source_router_id=source_router,
        target_router_id=target_router_id,
        source_agent=source_agent,
        tenant=tenant,
        intent=intent,
        task_id=task_id,
        hop_limit=8,
        body=body,
    )
    token = issue_relay_access_token(
        relay_id=relay_id,
        target_router_id=target_router_id,
        target_agent=target_agent,
        intent=intent,
        task_id=task_id,
        body=body,
    )
    request = Request(
        str(config["invoke_endpoint"]),
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/vnd.nexus.agent-envelope+json",
            "Accept": "application/json",
            "X-Nexus-Source-Router": source_router,
            "X-Nexus-Target-Router": target_router_id,
            "X-Nexus-Target-Agent": target_agent,
            "X-Nexus-Source-Agent": source_agent,
            "X-Nexus-Tenant": tenant,
            "X-Nexus-Intent": intent,
            "X-Nexus-Task-Id": task_id,
            "X-Nexus-Forwarding-Assertion": nfa,
        },
    )
    ca_file = str(config.get("ca_file") or getattr(settings, "NEXUS_RELAY_CA_FILE", "") or "")
    context = ssl.create_default_context(cafile=ca_file or None)
    cert_file = str(getattr(settings, "NEXUS_RELAY_CLIENT_CERT_FILE", "") or "")
    key_file = str(getattr(settings, "NEXUS_RELAY_CLIENT_KEY_FILE", "") or "")
    if not cert_file or not key_file:
        raise exceptions.APIException("Nexus Relay mTLS client identity is not configured.")
    context.load_cert_chain(certfile=cert_file, keyfile=key_file)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    try:
        with urlopen(request, timeout=timeout, context=context) as response:
            return response.status, dict(response.headers.items()), response.read()
    except HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read()
    except (URLError, OSError, TimeoutError) as exc:
        raise exceptions.APIException(f"Nexus Relay request failed: {exc}") from exc
