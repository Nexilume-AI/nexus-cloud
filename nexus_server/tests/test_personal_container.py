"""Container startup must preserve installation identity and generated secrets."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from nexus_personal import container


class ContainerBootstrapTests(unittest.TestCase):
    provider_environment = {'NEXUS_PROVIDER_COMPOSE_PROJECT': 'nexus-community',
                            'NEXUS_PROVIDER_COMPOSE_SERVICE': 'provider-controller'}

    def test_provider_data_uses_daemon_mount_source_not_windows_or_container_path(self):
        mounts = [{'Type': 'bind', 'Source': '/run/desktop/mnt/host/d/provider-data',
                   'Destination': '/var/lib/nexus-provider-runtimes', 'RW': True}]
        identity = 'a' * 64
        with patch.dict(os.environ, self.provider_environment), \
                patch('socket.gethostname', return_value='postgres-hostname'), \
                patch.object(container.subprocess, 'run', side_effect=[
                    SimpleNamespace(stdout=identity.encode()),
                    SimpleNamespace(stdout=json.dumps(mounts).encode())]) as run:
            self.assertEqual(container.provider_data_source(), '/run/desktop/mnt/host/d/provider-data')
        self.assertEqual(run.call_args.kwargs['timeout'], 5)
        self.assertEqual(run.call_args.args[0][:3], ['docker', 'container', 'inspect'])
        self.assertEqual(run.call_args.args[0][-1], identity)
        self.assertIn('label=com.docker.compose.project=nexus-community', run.call_args_list[0].args[0])
        self.assertIn('label=com.docker.compose.service=provider-controller', run.call_args_list[0].args[0])

    def test_provider_identity_does_not_fall_back_to_shared_hostname(self):
        for values in ({}, {**self.provider_environment, 'NEXUS_PROVIDER_COMPOSE_SERVICE': 'postgres'},
                       {**self.provider_environment, 'NEXUS_PROVIDER_COMPOSE_PROJECT': '--all'}):
            with self.subTest(values=values), patch.dict(os.environ, values, clear=True), \
                    patch.object(container.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, '^CONTAINER_PROVIDER_IDENTITY_INVALID$'):
                    container.provider_data_source()
                run.assert_not_called()

    def test_provider_identity_lookup_rejects_missing_ambiguous_and_invalid_ids(self):
        for output in (b'', b'a' * 64 + b'\n' + b'b' * 64, b'not-an-id'):
            with self.subTest(output=output), patch.dict(os.environ, self.provider_environment), \
                    patch.object(container.subprocess, 'run', return_value=SimpleNamespace(stdout=output)) as run:
                with self.assertRaisesRegex(ValueError, '^CONTAINER_PROVIDER_DATA_MAPPING_FAILED$'):
                    container.provider_data_source()
                run.assert_called_once()

    def test_provider_data_mapping_fails_closed_for_ambiguous_or_wrong_mounts(self):
        mount = {'Type': 'bind', 'Source': '/data/provider', 'Destination': '/var/lib/nexus-provider-runtimes', 'RW': True}
        for mounts in ([], [mount, mount], [{**mount, 'RW': False}], [{**mount, 'Source': '/'}], [{**mount, 'Source': '/data/../other'}]):
            with self.subTest(mounts=mounts), patch.dict(os.environ, self.provider_environment), \
                    patch.object(container.subprocess, 'run', side_effect=[SimpleNamespace(stdout=b'a' * 64),
                        SimpleNamespace(stdout=json.dumps(mounts).encode())]):
                with self.assertRaisesRegex(ValueError, '^CONTAINER_PROVIDER_DATA_MAPPING_FAILED$'):
                    container.provider_data_source()

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
