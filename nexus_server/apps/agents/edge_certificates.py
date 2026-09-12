from __future__ import annotations

from datetime import timedelta, timezone as datetime_timezone
from pathlib import Path
import re

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, ed448, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from django.conf import settings
from django.utils import timezone
from rest_framework import exceptions


def _load_device_ca() -> tuple[x509.Certificate, object]:
    certificate_path = Path(str(getattr(settings, "NEXUS_EDGE_DEVICE_CA_CERT_FILE", "") or ""))
    private_key_path = Path(str(getattr(settings, "NEXUS_EDGE_DEVICE_CA_KEY_FILE", "") or ""))
    if not certificate_path.is_file() or not private_key_path.is_file():
        raise exceptions.APIException(
            "Nexus device certificate authority is not configured. Set "
            "NEXUS_EDGE_DEVICE_CA_CERT_FILE and NEXUS_EDGE_DEVICE_CA_KEY_FILE."
        )
    try:
        certificate = x509.load_pem_x509_certificate(certificate_path.read_bytes())
        private_key = serialization.load_pem_private_key(private_key_path.read_bytes(), password=None)
    except (OSError, ValueError, TypeError) as exc:
        raise exceptions.APIException("Nexus device certificate authority could not be loaded.") from exc
    if certificate.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ) != private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ):
        raise exceptions.APIException("Nexus device certificate authority certificate and key do not match.")
    return certificate, private_key


def _load_ingress_ca() -> tuple[x509.Certificate, object]:
    certificate_path = Path(str(getattr(settings, "NEXUS_EDGE_CA_FILE", "") or ""))
    private_key_path = Path(str(getattr(settings, "NEXUS_EDGE_CA_KEY_FILE", "") or ""))
    if not certificate_path.is_file() or not private_key_path.is_file():
        raise exceptions.APIException(
            "Nexus Edge ingress certificate authority is not configured. Set "
            "NEXUS_EDGE_CA_FILE and NEXUS_EDGE_CA_KEY_FILE."
        )
    try:
        certificate = x509.load_pem_x509_certificate(certificate_path.read_bytes())
        private_key = serialization.load_pem_private_key(private_key_path.read_bytes(), password=None)
    except (OSError, ValueError, TypeError) as exc:
        raise exceptions.APIException("Nexus Edge ingress certificate authority could not be loaded.") from exc
    if certificate.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ) != private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ):
        raise exceptions.APIException("Nexus Edge ingress certificate authority certificate and key do not match.")
    return certificate, private_key


def _validated_csr(pem: str, *, field: str = "device_csr") -> x509.CertificateSigningRequest:
    try:
        csr = x509.load_pem_x509_csr(pem.encode("ascii"))
    except (UnicodeEncodeError, ValueError) as exc:
        raise exceptions.ValidationError({field: ["Enter a valid PEM certificate signing request."]}) from exc
    if not csr.is_signature_valid:
        raise exceptions.ValidationError({field: ["The certificate signing request signature is invalid."]})
    public_key = csr.public_key()
    if isinstance(public_key, rsa.RSAPublicKey) and public_key.key_size < 2048:
        raise exceptions.ValidationError({field: ["RSA keys must be at least 2048 bits."]})
    if not isinstance(public_key, (rsa.RSAPublicKey, ec.EllipticCurvePublicKey, ed25519.Ed25519PublicKey, ed448.Ed448PublicKey)):
        raise exceptions.ValidationError({field: ["The key algorithm is not supported."]})
    return csr


def issue_device_certificate(*, csr_pem: str, router_id: str, domain_id: str = "") -> dict[str, str]:
    csr = _validated_csr(csr_pem)
    ca_certificate, ca_private_key = _load_device_ca()
    lifetime_days = max(1, min(int(getattr(settings, "NEXUS_EDGE_DEVICE_CERT_DAYS", 90)), 365))
    now = timezone.now()
    not_after = min(now + timedelta(days=lifetime_days), ca_certificate.not_valid_after_utc)
    if not_after <= now + timedelta(hours=1):
        raise exceptions.APIException("Nexus device certificate authority is expired or expires too soon.")
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Nexus Edge"),
            x509.NameAttribute(NameOID.COMMON_NAME, router_id),
        ]
    )
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_certificate.subject)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(csr.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_private_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=isinstance(csr.public_key(), rsa.RSAPublicKey),
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
    )
    identities: list[x509.GeneralName] = [x509.UniformResourceIdentifier(f"urn:nexus:router:{router_id}")]
    if domain_id:
        identities.append(x509.DNSName(f"{router_id}.{domain_id}".lower()))
    builder = builder.add_extension(x509.SubjectAlternativeName(identities), critical=False)
    algorithm = None if isinstance(ca_private_key, (ed25519.Ed25519PrivateKey, ed448.Ed448PrivateKey)) else hashes.SHA256()
    certificate = builder.sign(private_key=ca_private_key, algorithm=algorithm)
    certificate_pem = certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")
    ca_pem = ca_certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")
    return {
        "certificate": certificate_pem,
        "certificate_chain": certificate_pem + ca_pem,
        "ca_certificate": ca_pem,
        "sha256": certificate.fingerprint(hashes.SHA256()).hex(),
        "not_after": certificate.not_valid_after_utc.astimezone(datetime_timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def issue_ingress_certificate(*, csr_pem: str, node_id: str) -> dict[str, str]:
    """Issue a server-only identity for one authenticated EdgeNode."""

    csr = _validated_csr(csr_pem, field="ingress_csr")
    ca_certificate, ca_private_key = _load_ingress_ca()
    bundle_id = str(getattr(settings, "NEXUS_EDGE_INGRESS_CA_BUNDLE_ID", "edge-local-ca") or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}", bundle_id):
        raise exceptions.APIException("Nexus Edge ingress CA bundle ID is invalid.")
    lifetime_days = max(1, min(int(getattr(settings, "NEXUS_EDGE_INGRESS_CERT_DAYS", 90)), 365))
    now = timezone.now()
    not_after = min(now + timedelta(days=lifetime_days), ca_certificate.not_valid_after_utc)
    if not_after <= now + timedelta(hours=1):
        raise exceptions.APIException("Nexus Edge ingress certificate authority is expired or expires too soon.")

    normalized_node_id = str(node_id).replace("-", "").lower()
    if not re.fullmatch(r"[0-9a-f]{32}", normalized_node_id):
        raise exceptions.APIException("Nexus Edge node identity is invalid.")
    tls_server_name = f"edge-{normalized_node_id}.router.nexus"
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Nexus Edge"),
            x509.NameAttribute(NameOID.COMMON_NAME, tls_server_name),
        ]
    )
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_certificate.subject)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(csr.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_private_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=isinstance(csr.public_key(), rsa.RSAPublicKey),
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(tls_server_name)]), critical=False)
    )
    algorithm = None if isinstance(ca_private_key, (ed25519.Ed25519PrivateKey, ed448.Ed448PrivateKey)) else hashes.SHA256()
    certificate = builder.sign(private_key=ca_private_key, algorithm=algorithm)
    certificate_pem = certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")
    ca_pem = ca_certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")
    return {
        "certificate": certificate_pem,
        "certificate_chain": certificate_pem + ca_pem,
        "ca_certificate": ca_pem,
        "sha256": certificate.fingerprint(hashes.SHA256()).hex(),
        "not_after": certificate.not_valid_after_utc.astimezone(datetime_timezone.utc).isoformat().replace("+00:00", "Z"),
        "tls_server_name": tls_server_name,
        "ca_bundle_id": bundle_id,
    }
