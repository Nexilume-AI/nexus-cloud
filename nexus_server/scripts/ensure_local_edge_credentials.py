#!/usr/bin/env python3
"""Create or reuse durable credentials for the local Nexus Edge front door."""

from __future__ import annotations

import argparse
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
    "website-ca.pem",
    "website-ca.key",
    "website.pem",
    "website.key",
    "device-ca.pem",
    "device-ca.key",
    "edge-ingress-ca.pem",
    "edge-ingress-ca.key",
    "edge-client.pem",
    "edge-client.key",
    "edge-jwt.key",
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


def _ca(common_name: str, *, days: int = 3650):
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=10))
        .not_valid_after(now + timedelta(days=days))
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
    return key, cert


def _leaf(
    *,
    authority_key,
    authority_cert: x509.Certificate,
    common_name: str,
    dns_names: list[str],
    ip_addresses: list[str],
    server: bool,
    client: bool,
    days: int = 825,
):
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)
    purposes = []
    if server:
        purposes.append(ExtendedKeyUsageOID.SERVER_AUTH)
    if client:
        purposes.append(ExtendedKeyUsageOID.CLIENT_AUTH)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .issuer_name(authority_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=10))
        .not_valid_after(now + timedelta(days=days))
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
    )
    return key, builder.sign(authority_key, hashes.SHA256())


def _write_identity(directory: Path, name: str, key, certificate: x509.Certificate) -> None:
    _write(directory / f"{name}.key", _private_bytes(key), 0o600)
    _write(directory / f"{name}.pem", certificate.public_bytes(serialization.Encoding.PEM), 0o644)


def _certificate_expiry(certificate: x509.Certificate) -> datetime:
    value = getattr(certificate, "not_valid_after_utc", None)
    if value is not None:
        return value
    return certificate.not_valid_after.replace(tzinfo=timezone.utc)


def _reusable(state_dir: Path, host_address: str, cloud_port: int) -> dict | None:
    current_path = state_dir / "current.json"
    if not current_path.is_file():
        return None
    try:
        current = json.loads(current_path.read_text(encoding="utf-8"))
        credential_dir = Path(current["credential_dir"]).resolve()
        manifest = json.loads((credential_dir / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("host_address") != host_address or int(manifest.get("cloud_port", 0)) != cloud_port:
            return None
        if any(not (credential_dir / name).is_file() for name in REQUIRED_FILES):
            return None
        ca_certificate = x509.load_pem_x509_certificate((credential_dir / "website-ca.pem").read_bytes())
        certificate = x509.load_pem_x509_certificate((credential_dir / "website.pem").read_bytes())
        ca_subject_key = ca_certificate.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
        authority_key = certificate.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier).value
        if authority_key.key_identifier != ca_subject_key.digest:
            return None
        if _certificate_expiry(certificate) <= datetime.now(timezone.utc) + timedelta(days=30):
            return None
        serialization.load_pem_private_key((credential_dir / "edge-jwt.key").read_bytes(), password=None)
        return {**manifest, "credential_dir": str(credential_dir), "reused": True}
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError, x509.ExtensionNotFound):
        return None


def ensure_credentials(state_dir: Path, host_address: str, cloud_port: int) -> dict:
    ipaddress.ip_address(host_address)
    existing = _reusable(state_dir, host_address, cloud_port)
    if existing is not None:
        return existing

    state_dir.mkdir(parents=True, exist_ok=True)
    generation = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
    credential_dir = (state_dir / "generations" / generation).resolve()
    credential_dir.mkdir(parents=True, exist_ok=False)
    try:
        os.chmod(credential_dir, 0o700)
    except OSError:
        pass

    website_ca_key, website_ca = _ca("Nexus Local Cloud Website CA")
    website_key, website = _leaf(
        authority_key=website_ca_key,
        authority_cert=website_ca,
        common_name="nexus-cloud.local",
        dns_names=["nexus-cloud.local", "localhost"],
        ip_addresses=[host_address, "127.0.0.1"],
        server=True,
        client=False,
    )
    device_ca_key, device_ca = _ca("Nexus Local Edge Device CA")
    ingress_ca_key, ingress_ca = _ca("Nexus Local Edge Ingress CA")
    edge_client_key, edge_client = _leaf(
        authority_key=ingress_ca_key,
        authority_cert=ingress_ca,
        common_name="nexus-local-edge-client",
        dns_names=[],
        ip_addresses=[],
        server=False,
        client=True,
    )

    _write_identity(credential_dir, "website-ca", website_ca_key, website_ca)
    _write_identity(credential_dir, "website", website_key, website)
    _write_identity(credential_dir, "device-ca", device_ca_key, device_ca)
    _write_identity(credential_dir, "edge-ingress-ca", ingress_ca_key, ingress_ca)
    _write_identity(credential_dir, "edge-client", edge_client_key, edge_client)

    jwt_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    _write(credential_dir / "edge-jwt.key", _private_bytes(jwt_key), 0o600)
    manifest = {
        "credential_dir": str(credential_dir),
        "host_address": host_address,
        "cloud_port": cloud_port,
        "public_base_url": f"https://{host_address}:{cloud_port}",
        "issuer": f"https://{host_address}:{cloud_port}/edge",
        "key_id": f"edge-local-{secrets.token_hex(6)}",
        "website_ca": str(credential_dir / "website-ca.pem"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reused": False,
    }
    (credential_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (state_dir / "current.json").write_text(
        json.dumps({"credential_dir": str(credential_dir)}, indent=2), encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--host-address", required=True)
    parser.add_argument("--cloud-port", type=int, default=28443)
    args = parser.parse_args()
    if args.cloud_port < 1 or args.cloud_port > 65535:
        parser.error("--cloud-port must be between 1 and 65535")
    result = ensure_credentials(args.state_dir.resolve(), args.host_address, args.cloud_port)
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
