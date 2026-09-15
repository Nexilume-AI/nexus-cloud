"""Container startup must preserve installation identity and generated secrets."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nexus_personal import container


class ContainerBootstrapTests(unittest.TestCase):
    def test_non_loopback_plaintext_and_credential_origins_rejected(self):
        for origin in ('http://0.0.0.0:18090', 'http://example.com', 'https://user:secret@example.com',
                       'https://example.com/path', 'https://example.com/?token=secret'):
            with self.subTest(origin=origin), patch.dict(os.environ, {'NEXUS_ORIGIN': origin}):
                with self.assertRaises(ValueError):
                    container.options()

    def test_explicit_local_and_https_origins(self):
        with patch.dict(os.environ, {'NEXUS_ORIGIN': 'http://127.0.0.1:19090'}):
            self.assertEqual(container.options(), ('http://127.0.0.1:19090', 19090, True))
        with patch.dict(os.environ, {'NEXUS_ORIGIN': 'https://cloud.example'}):
            self.assertEqual(container.options(), ('https://cloud.example', 443, False))

    def test_restart_does_not_regenerate_password_and_changed_identity_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap, installation = root / 'bootstrap', root / 'state' / 'installation'
            bootstrap.mkdir()
            installation.parent.mkdir()
            with patch.multiple(container, BOOTSTRAP=bootstrap, INSTALLATION=installation), \
                 patch.object(container, 'initialize_volumes'), patch.object(container, 'prepare_relay'), \
                 patch.object(container, 'prepare') as prepare, patch.object(container, 'check') as check, \
                 patch.dict(os.environ, {'NEXUS_ORIGIN': 'http://127.0.0.1:18090', 'NEXUS_OWNER_EMAIL': 'owner@example.test'}):
                container.prepare_installation()
                password = (bootstrap / 'owner-password').read_bytes()
                self.assertGreaterEqual(len(password), 32)
                self.assertNotEqual(password, (bootstrap / 'database-password').read_bytes())
                installation.mkdir()
                with patch('nexus_personal.host_config.read_protected_json', side_effect=lambda p: json.loads(Path(p).read_text())):
                    container.prepare_installation()
                    prepare.assert_called_once()
                    check.assert_called_once()
                    self.assertEqual((bootstrap / 'owner-password').read_bytes(), password)
                    with patch.dict(os.environ, {'NEXUS_OWNER_EMAIL': 'different@example.test'}):
                        with self.assertRaisesRegex(ValueError, 'CONTAINER_IDENTITY_CHANGED'):
                            container.prepare_installation()

    def test_partial_bootstrap_not_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'database-password').write_text('existing')
            with patch.multiple(container, BOOTSTRAP=root, INSTALLATION=root / 'installation'), \
                 patch.object(container, 'initialize_volumes'):
                with self.assertRaisesRegex(ValueError, 'CONTAINER_BOOTSTRAP_INCOMPLETE'):
                    container.prepare_installation()
            self.assertEqual((root / 'database-password').read_text(), 'existing')


if __name__ == '__main__':
    unittest.main()
