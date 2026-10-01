"""Installer unit tests with real protected files and an isolated DB transaction stub."""
import hashlib
import json
import unittest
from unittest.mock import MagicMock, patch

from nexus_personal import install
from tests import test_personal_install as fixtures


class ProviderInstallTests(unittest.TestCase):
    setUp = fixtures.PersonalInstallationPreparationTests.setUp
    write_infra = fixtures.PersonalInstallationPreparationTests.write_infra
    write_manifest = fixtures.PersonalInstallationPreparationTests.write_manifest
    prepare = fixtures.PersonalInstallationPreparationTests.prepare

    def inputs(self):
        releases = self.root / 'releases'
        releases.mkdir()
        self.prepare()
        return dict(directory=self.destination, release_dir=releases, engines=('codex_proxy',), controller_port=43108)

    def database(self, before):
        connection = MagicMock()
        config = json.loads(before)
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [(True,), (config['instance_id'], hashlib.sha256(before).hexdigest(), 'initialized')]
        return connection

    def test_read_only_check_does_not_write_or_connect_to_database(self):
        inputs = self.inputs()
        before = (self.destination / 'host.json').read_bytes()
        with patch('apps.providers.execution_setup.inspect_execution', return_value={'codex_proxy': {'available': True}}), \
                patch('psycopg.connect', side_effect=AssertionError('Read-only')):
            result = install.enable_provider_runtime(**inputs, check_only=True)
        self.assertFalse(result['provider_configured'])
        self.assertFalse(result['restart_required'])
        self.assertEqual((self.destination / 'host.json').read_bytes(), before)

    def test_setup_preserves_identity_and_repeated_setup_reuses_token(self):
        inputs = self.inputs()
        path = self.destination / 'host.json'
        before = path.read_bytes()
        connection = self.database(before)
        with patch('apps.providers.execution_setup.inspect_execution', return_value={'codex_proxy': {'available': True}}), \
                patch('psycopg.connect', return_value=connection) as connect:
            result = install.enable_provider_runtime(**inputs)
            after = path.read_bytes()
            second = install.enable_provider_runtime(**inputs)
        self.assertTrue(result['restart_required'])
        self.assertEqual(result['controllers_configured'], ['provider'])
        self.assertFalse(second['restart_required'])
        self.assertEqual(path.read_bytes(), after)
        connect.assert_called_once()
        connection.commit.assert_called_once()
        original, updated = json.loads(before), json.loads(after)
        self.assertEqual({key: value for key, value in updated.items() if key != 'controllers'},
                         {key: value for key, value in original.items() if key != 'controllers'})
        self.assertEqual(len(updated['controllers']['provider']['token']), 64)
        self.assertEqual(install.check(directory=self.destination)['controllers_configured'], ['provider'])

    def test_preflight_failure_leaves_configuration_and_receipt_intact(self):
        inputs = self.inputs()
        before = [(self.destination / name).read_bytes() for name in ('host.json', 'installation.json')]
        with patch('apps.providers.execution_setup.inspect_execution', return_value={'codex_proxy': {'available': False, 'code': 'PROVIDER_IMAGE_UNAVAILABLE'}}), \
                patch('psycopg.connect', side_effect=AssertionError('No mutation')):
            with self.assertRaisesRegex(install.InstallationError, 'PROVIDER_IMAGE_UNAVAILABLE'):
                install.enable_provider_runtime(**inputs)
        self.assertEqual([(self.destination / name).read_bytes() for name in ('host.json', 'installation.json')], before)

    def test_commit_failure_restores_both_files_and_removes_owned_marker(self):
        inputs = self.inputs()
        before = [(self.destination / name).read_bytes() for name in ('host.json', 'installation.json')]
        connection = self.database(before[0])
        connection.commit.side_effect = RuntimeError('private-db-error')
        with patch('apps.providers.execution_setup.inspect_execution', return_value={'codex_proxy': {'available': True}}), \
                patch('psycopg.connect', return_value=connection):
            with self.assertRaisesRegex(install.InstallationError, '^INSTALL_RECONFIGURATION_FAILED$'):
                install.enable_provider_runtime(**inputs)
        self.assertEqual([(self.destination / name).read_bytes() for name in ('host.json', 'installation.json')], before)
        self.assertFalse((self.destination / 'installation-reconfigure.json').exists())
        connection.rollback.assert_called_once()

    def test_foreign_marker_is_never_removed(self):
        self.inputs()
        path = self.destination / 'host.json'
        marker = self.destination / 'installation-reconfigure.json'
        marker.write_text('another installer owns this')
        with patch('psycopg.connect', side_effect=AssertionError('No DB')):
            with self.assertRaises(install.InstallationError):
                install._reconfigure(self.destination, json.loads(path.read_bytes()), operation='fixture', expected_host=path.read_bytes())
        self.assertEqual(marker.read_text(), 'another installer owns this')

    def test_concurrent_external_edit_is_not_overwritten(self):
        self.inputs()
        path = self.destination / 'host.json'
        before = path.read_bytes()
        path.write_bytes(before + b'\n')
        with self.assertRaisesRegex(install.InstallationError, 'INSTALL_RECONFIGURATION_CONFLICT'):
            install._reconfigure(self.destination, json.loads(before), operation='fixture', expected_host=before)
        self.assertEqual(path.read_bytes(), before + b'\n')
