from __future__ import annotations

import importlib.util
import ipaddress
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from django.test import SimpleTestCase


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ensure_local_edge_credentials.py"
SPEC = importlib.util.spec_from_file_location("ensure_local_edge_credentials", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LocalEdgeCredentialsScriptTests(SimpleTestCase):
    def test_generates_and_reuses_durable_local_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary)
            first = MODULE.ensure_credentials(state_dir, "192.168.250.164", 28443)
            second = MODULE.ensure_credentials(state_dir, "192.168.250.164", 28443)

            self.assertFalse(first["reused"])
            self.assertTrue(second["reused"])
            self.assertEqual(first["credential_dir"], second["credential_dir"])
            directory = Path(first["credential_dir"])
            self.assertTrue(all((directory / name).is_file() for name in MODULE.REQUIRED_FILES))

            certificate = x509.load_pem_x509_certificate((directory / "website.pem").read_bytes())
            ca_certificate = x509.load_pem_x509_certificate((directory / "website-ca.pem").read_bytes())
            san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            self.assertIn(ipaddress.ip_address("192.168.250.164"), san.get_values_for_type(x509.IPAddress))
            subject_key = ca_certificate.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
            authority_key = certificate.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier).value
            self.assertEqual(authority_key.key_identifier, subject_key.digest)
            expiry = MODULE._certificate_expiry(certificate)
            self.assertGreater(expiry, datetime.now(timezone.utc) + timedelta(days=365))

            jwt_key = serialization.load_pem_private_key((directory / "edge-jwt.key").read_bytes(), password=None)
            self.assertGreaterEqual(jwt_key.key_size, 2048)

    def test_rotates_when_cloud_address_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary)
            first = MODULE.ensure_credentials(state_dir, "192.168.250.164", 28443)
            second = MODULE.ensure_credentials(state_dir, "192.168.250.165", 28443)

            self.assertNotEqual(first["credential_dir"], second["credential_dir"])
            self.assertFalse(second["reused"])
