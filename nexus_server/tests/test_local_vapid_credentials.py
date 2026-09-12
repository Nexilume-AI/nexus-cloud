import json
import subprocess
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.test import SimpleTestCase


class LocalVapidCredentialTests(SimpleTestCase):
    def test_generator_reuses_p256_key_without_emitting_private_material(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "ensure_local_vapid_credentials.py"
        with tempfile.TemporaryDirectory() as directory:
            command = [sys.executable, str(script), "--state-dir", directory]
            first = subprocess.run(command, capture_output=True, text=True, check=True)
            second = subprocess.run(command, capture_output=True, text=True, check=True)
            first_data, second_data = json.loads(first.stdout), json.loads(second.stdout)
            self.assertEqual(first_data, second_data)
            self.assertNotIn("PRIVATE KEY", first.stdout)
            private_path = Path(first_data["private_key_file"])
            key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
            self.assertIsInstance(key, ec.EllipticCurvePrivateKey)
            self.assertIsInstance(key.curve, ec.SECP256R1)
            self.assertEqual(len(first_data["public_key"]), 87)
