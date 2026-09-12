from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Create the private CA used to issue managed OpenWrt device client certificates."

    def add_arguments(self, parser):
        parser.add_argument("--cert", required=True, help="Output PEM CA certificate path")
        parser.add_argument("--key", required=True, help="Output PEM private key path")
        parser.add_argument("--common-name", default="Nexus Edge Device CA")
        parser.add_argument("--days", type=int, default=3650)

    def handle(self, *args, **options):
        certificate_path = Path(options["cert"]).expanduser().resolve()
        private_key_path = Path(options["key"]).expanduser().resolve()
        if certificate_path.exists() or private_key_path.exists():
            raise CommandError("Refusing to overwrite an existing CA certificate or private key.")
        days = int(options["days"])
        if days < 30 or days > 7300:
            raise CommandError("--days must be between 30 and 7300.")
        certificate_path.parent.mkdir(parents=True, exist_ok=True)
        private_key_path.parent.mkdir(parents=True, exist_ok=True)
        private_key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, str(options["common_name"]))])
        now = timezone.now()
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=days))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
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
            .sign(private_key, hashes.SHA256())
        )
        private_key_path.write_bytes(
            private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        os.chmod(private_key_path, 0o600)
        certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        os.chmod(certificate_path, 0o644)
        self.stdout.write(self.style.SUCCESS(f"Created device CA certificate: {certificate_path}"))
        self.stdout.write(self.style.SUCCESS(f"Created device CA private key: {private_key_path}"))
        self.stdout.write("Configure the reverse proxy to trust the CA certificate for Edge device mTLS.")
