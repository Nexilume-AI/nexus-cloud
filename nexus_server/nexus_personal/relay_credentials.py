#!/usr/bin/env python3
"""Create or reuse durable credentials for the local Nexus Cloud Relay."""

from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


REQUIRED_FILES = (
    "relay-server-ca.pem",
    "relay-server-ca.key",
    "server-client-ca.pem",
    "server-client-ca.key",
    "relay-tunnel.pem",
    "relay-tunnel.key",
    "relay-cloud.pem",
    "relay-cloud.key",
    "server-relay-client.pem",
    "server-relay-client.key",
    "relay-jwt.key",
    "relay-jwt-public.pem",
    "forwarding.key",
    "forwarding-public.pem",
    "manifest.json",
)


def _private_bytes(key: object) -> bytes:
    return key.private_bytes(  # type: ignore[union-attr]
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _write(path: Path, value: bytes, mode: int) -> None:
    path.write_bytes(value)
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _ca(common_name: str):
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=10))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return key, certificate


def _leaf(
    *,
    authority_key,
    authority_cert: x509.Certificate,
    common_name: str,
    dns_names: list[str],
    ip_addresses: list[str],
    server: bool,
    client: bool,
):
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)
    purposes = []
    if server:
        purposes.append(ExtendedKeyUsageOID.SERVER_AUTH)
    if client:
        purposes.append(ExtendedKeyUsageOID.CLIENT_AUTH)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .issuer_name(authority_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=10))
        .not_valid_after(now + timedelta(days=825))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(authority_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(value) for value in dns_names]
                + [x509.IPAddress(ipaddress.ip_address(value)) for value in ip_addresses]
            ),
            critical=False,
        )
        .add_extension(x509.ExtendedKeyUsage(purposes), critical=False)
        .sign(authority_key, hashes.SHA256())
    )
    return key, certificate


def _write_identity(directory: Path, name: str, key, certificate: x509.Certificate) -> None:
    _write(directory / f"{name}.key", _private_bytes(key), 0o600)
    _write(directory / f"{name}.pem", certificate.public_bytes(serialization.Encoding.PEM), 0o644)


def _device_ca_fingerprint(edge_credential_dir: Path) -> str:
    certificate = x509.load_pem_x509_certificate((edge_credential_dir / "device-ca.pem").read_bytes())
    return certificate.fingerprint(hashes.SHA256()).hex()


def _reusable(
    state_dir: Path,
    *,
    host_address: str,
    tunnel_port: int,
    cloud_port: int,
    device_ca_fingerprint: str,
) -> dict | None:
    current_path = state_dir / "current.json"
    if not current_path.is_file():
        return None
    try:
        current = json.loads(current_path.read_text(encoding="utf-8"))
        directory = Path(current["credential_dir"]).resolve()
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if (
            manifest.get("host_address") != host_address
            or int(manifest.get("tunnel_port", 0)) != tunnel_port
            or int(manifest.get("cloud_port", 0)) != cloud_port
            or manifest.get("device_ca_sha256") != device_ca_fingerprint
            or any(not (directory / name).is_file() for name in REQUIRED_FILES)
        ):
            return None
        x509.load_pem_x509_certificate((directory / "relay-tunnel.pem").read_bytes())
        serialization.load_pem_private_key((directory / "relay-jwt.key").read_bytes(), password=None)
        return {**manifest, "credential_dir": str(directory), "reused": True}
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def ensure_credentials(
    state_dir: Path,
    *,
    edge_credential_dir: Path,
    host_address: str,
    tunnel_port: int,
    cloud_port: int,
) -> dict:
    ipaddress.ip_address(host_address)
    fingerprint = _device_ca_fingerprint(edge_credential_dir)
    existing = _reusable(
        state_dir,
        host_address=host_address,
        tunnel_port=tunnel_port,
        cloud_port=cloud_port,
        device_ca_fingerprint=fingerprint,
    )
    if existing is not None:
        return existing

    state_dir.mkdir(parents=True, exist_ok=True)
    generation = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
    directory = (state_dir / "generations" / generation).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass

    relay_ca_key, relay_ca = _ca("Nexus Local Relay Server CA")
    client_ca_key, client_ca = _ca("Nexus Local Relay Cloud Client CA")
    tunnel_key, tunnel_certificate = _leaf(
        authority_key=relay_ca_key,
        authority_cert=relay_ca,
        common_name="relay.nexus-cloud.local",
        dns_names=["relay.nexus-cloud.local"],
        ip_addresses=[host_address],
        server=True,
        client=False,
    )
    cloud_key, cloud_certificate = _leaf(
        authority_key=relay_ca_key,
        authority_cert=relay_ca,
        common_name="relay-cloud.nexus-cloud.local",
        dns_names=["relay-cloud.nexus-cloud.local", "localhost"],
        ip_addresses=["127.0.0.1", host_address],
        server=True,
        client=False,
    )
    client_dns = "nexus-cloud-relay-client.local"
    client_key, client_certificate = _leaf(
        authority_key=client_ca_key,
        authority_cert=client_ca,
        common_name=client_dns,
        dns_names=[client_dns],
        ip_addresses=[],
        server=False,
        client=True,
    )
    _write_identity(directory, "relay-server-ca", relay_ca_key, relay_ca)
    _write_identity(directory, "server-client-ca", client_ca_key, client_ca)
    _write_identity(directory, "relay-tunnel", tunnel_key, tunnel_certificate)
    _write_identity(directory, "relay-cloud", cloud_key, cloud_certificate)
    _write_identity(directory, "server-relay-client", client_key, client_certificate)

    relay_jwt = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    forwarding = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    _write(directory / "relay-jwt.key", _private_bytes(relay_jwt), 0o600)
    _write(
        directory / "relay-jwt-public.pem",
        relay_jwt.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ),
        0o644,
    )
    _write(directory / "forwarding.key", _private_bytes(forwarding), 0o600)
    _write(
        directory / "forwarding-public.pem",
        forwarding.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ),
        0o644,
    )

    ticket_key_id = "relay-ticket-local-1"
    ticket_secret = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
    manifest = {
        "credential_dir": str(directory),
        "host_address": host_address,
        "tunnel_port": tunnel_port,
        "cloud_port": cloud_port,
        "device_ca_sha256": fingerprint,
        "relay_id": "relay-local",
        "relay_router_id": "relay-router-local",
        "relay_domain_id": "relay.local",
        "relay_client_dns": client_dns,
        "relay_issuer": "https://nexus-cloud.local/relay",
        "relay_key_id": "relay-rs256-local-1",
        "forwarding_key_id": "nexus-cloud-local-1",
        "forwarding_issuer": "nexus-cloud-local",
        "ticket_key_id": ticket_key_id,
        "ticket_keys": {ticket_key_id: ticket_secret},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reused": False,
    }
    _write(directory / "manifest.json", json.dumps(manifest, indent=2).encode("utf-8"), 0o600)
    (state_dir / "current.json").write_text(
        json.dumps({"credential_dir": str(directory)}, indent=2), encoding="utf-8"
    )
    return manifest

