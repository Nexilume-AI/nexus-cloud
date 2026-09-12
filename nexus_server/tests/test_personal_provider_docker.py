"""Opt-in installed Personal Controller acceptance using an approved real image.

Reuses the installation/PostgreSQL fixture, never a second Cloud API, mocked
runner, development-image bypass, or business database/container.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from uuid import UUID

from tests.test_personal_controllers import DENY_PRIVATE
from tests import test_personal_initialize as initialization


class PersonalProviderDockerTests(unittest.TestCase):
    setUp = initialization.PersonalInitializationPostgresTests.setUp
    write_infra = initialization.PersonalInitializationPostgresTests.write_infra
    write_manifest = initialization.PersonalInitializationPostgresTests.write_manifest
    prepare = initialization.PersonalInitializationPostgresTests.prepare
    database = initialization.PersonalInitializationPostgresTests.database
    initialize = initialization.PersonalInitializationPostgresTests.initialize

    def docker(self, *arguments):
        return subprocess.run(['docker', *arguments], capture_output=True, text=True, timeout=30)

    @unittest.skipUnless(os.environ.get('NEXUS_PERSONAL_TEST_PROVIDER_RELEASE_DIR'),
                         'Explicit approved Codex Proxy release acceptance required')
    def test_installed_owner_starts_checks_and_stops_verified_provider(self):
        self.check_provider('codex_proxy', 'NEXUS_PERSONAL_TEST_PROVIDER_RELEASE_DIR')

    @unittest.skipUnless(os.environ.get('NEXUS_PERSONAL_TEST_CLIPROXYAPI_RELEASE_DIR'),
                         'Explicit approved CLIProxyAPI release acceptance required')
    def test_installed_owner_starts_checks_and_stops_verified_cliproxyapi(self):
        self.check_provider('cliproxyapi', 'NEXUS_PERSONAL_TEST_CLIPROXYAPI_RELEASE_DIR')

    def check_provider(self, provider, opt_in):
        self.assertTrue(os.environ.get('NEXUS_PERSONAL_TEST_POSTGRES_CONFIG'),
                        'Opted-in Docker acceptance requires isolated PostgreSQL')
        self.assertTrue(os.environ.get('NEXUS_PERSONAL_TEST_REDIS_PORT'),
                        'Real recovery acceptance requires an explicit owned Redis port')
        releases = Path(os.environ[opt_in]).resolve(strict=True)
        self.assertTrue((releases / (provider + '.json')).is_file(), 'Approved receipt required')
        self.assertEqual(self.docker('version', '--format', '{{.Server.Version}}').returncode, 0,
                         'Docker Engine must be available when acceptance is opted in')
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        self.infra['controllers'] = {'provider': {'port': port, 'release_dir': str(releases)}}
        with self.database() as app:
            result = self.initialize()
            self.assertEqual(result.returncode, 0, result.stderr)
            environment = {**os.environ, 'NEXUS_PERSONAL_CONFIG': str(self.destination / 'host.json'),
                           'DJANGO_SETTINGS_MODULE': 'nexus_personal.settings'}
            # Child output is discarded: a crash must not dump host credentials.
            # The probe reports only bounded, secret-free lifecycle assertions.
            controller = subprocess.Popen([sys.executable, '-c', DENY_PRIVATE +
                '\nfrom nexus_personal.processes import main; main(["provider-controller"])'],
                env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                code = DENY_PRIVATE + '\nfrom tests.personal_provider_controller_probe import run; run(' + repr(provider) + ')'
                result = subprocess.run([sys.executable, '-c', code], env=environment,
                                        capture_output=True, text=True, timeout=120)
                config = json.loads((self.destination / 'host.json').read_bytes())
                for secret in (config['controllers']['provider']['token'], self.infra['database']['password']):
                    self.assertNotIn(secret, result.stdout + result.stderr)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), 'installed-provider-controller-docker-ok')
                self.assertIsNone(controller.poll(), 'Controller must remain alive after lifecycle requests')
            finally:
                # Query only the freshly created fixture DB. Validate exact UUID
                # names; never enumerate/delete containers by a shared prefix.
                try:
                    rows = app.execute('SELECT id FROM providers_providerruntimeaccount').fetchall()
                    for (runtime_id,) in rows:
                        name = 'nexus-provider-runtime-' + str(UUID(str(runtime_id)))
                        found = self.docker('ps', '-aq', '--filter', 'name=^/' + name + '$')
                        self.assertEqual(found.returncode, 0, 'Container cleanup inventory failed')
                        if found.stdout.strip():
                            removed = self.docker('rm', '-f', name)
                            self.assertEqual(removed.returncode, 0, 'Owned test container cleanup failed')
                        remaining = self.docker('ps', '-aq', '--filter', 'name=^/' + name + '$')
                        self.assertEqual(remaining.returncode, 0)
                        self.assertEqual(remaining.stdout.strip(), '', 'Owned container survived cleanup')
                finally:
                    if controller.poll() is None:
                        controller.terminate()
                    try:
                        controller.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        controller.kill()
                        controller.wait(timeout=15)
