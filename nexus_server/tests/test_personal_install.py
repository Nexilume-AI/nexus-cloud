"""Real private directory/ACL, config and verified-asset installation fixtures.

No Docker, SSH, fake runner, database or Cloud process is used by preparation.
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from nexus_personal import install
from nexus_personal.host_config import load_config
from tests import test_personal_host as fixtures


class PersonalInstallationPreparationTests(unittest.TestCase):
    def setUp(self):
        fixtures.PersonalHostTests.setUp(self)
        self.infrastructure = self.root / 'infrastructure.json'
        self.infra = {'schema_version': 1, 'database': self.config['database'], 'redis_url': self.config['redis_url']}
        self.write_infra()
        self.bundle = self.root / 'bundle'
        self.bundle.mkdir(mode=0o700)
        self.assets = {'index.html': b'<html>Community fixture</html>', 'assets/main.js': b'/* local fixture */'}
        self.manifest = {'schema_version': 1, 'distribution': 'community', 'files': {}}
        for name, body in self.assets.items():
            path = self.bundle / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            self.manifest['files'][name] = {'size': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
        self.write_manifest()
        self.destination = self.root / 'prepared'

    def write_infra(self):
        self.infrastructure.write_text(json.dumps(self.infra), encoding='utf-8')
        self.infrastructure.chmod(0o600)

    def write_manifest(self):
        (self.bundle / install.MANIFEST).write_text(json.dumps(self.manifest), encoding='utf-8')

    def prepare(self, **changes):
        return install.prepare(**{'directory': self.destination, 'origin': 'https://personal.example:9443/',
            'infrastructure_file': self.infrastructure, 'web_bundle': self.bundle, **changes})

    def test_real_prepare_private_material_and_check_without_any_service_adoption(self):
        (self.bundle / 'not-allowlisted.txt').write_text('do not copy', encoding='utf-8')
        with patch('psycopg.connect', side_effect=AssertionError('No database access')):
            result = self.prepare()
        config = load_config({'NEXUS_PERSONAL_CONFIG': str(self.destination / 'host.json')})
        self.assertEqual(config['public_origin'], 'https://personal.example:9443')
        self.assertEqual(config['state_dir'], self.destination)
        self.assertEqual(config['database'], self.infra['database'])
        self.assertEqual(result['state'], 'prepared')
        self.assertFalse(result['services_started_by_command'])
        self.assertEqual(result['service_health'], 'not_checked')
        self.assertEqual(result['database_state'], 'not_checked')
        self.assertEqual(result['controllers_configured'], [])
        self.assertFalse(result['python_builder_enabled'])
        self.assertEqual(install.check(directory=self.destination), result)
        self.assertFalse((self.destination / 'installation-incomplete.json').exists())
        self.assertFalse((self.destination / 'web' / 'not-allowlisted.txt').exists())
        for name, body in self.assets.items():
            self.assertEqual((self.destination / 'web' / name).read_bytes(), body)
        if os.name != 'nt':
            self.assertEqual((self.destination.stat().st_mode & 0o777), 0o700)
            self.assertEqual(((self.destination / 'host.json').stat().st_mode & 0o777), 0o600)

    def test_independent_secrets_and_ignored_cloud_environment(self):
        with patch.dict(os.environ, {'DATABASE_URL': 'private-cloud-sentinel', 'POSTGRES_PASSWORD': 'private-cloud-sentinel',
                'NEXUS_PERSONAL_CONFIG': 'not-an-input', 'NEXUS_SECRET_ENCRYPTION_KEYS': 'private-cloud-sentinel'}):
            self.prepare()
            second = self.root / 'second'
            self.prepare(directory=second)
        first_config = json.loads((self.destination / 'host.json').read_bytes())
        second_config = json.loads((second / 'host.json').read_bytes())
        for name in ('secret_key', 'encryption_key', 'instance_id'):
            self.assertNotEqual(first_config[name], second_config[name])
        self.assertNotIn('private-cloud-sentinel', (self.destination / 'host.json').read_text())

    def test_controller_tokens_are_generated_without_claiming_egress_or_release_readiness(self):
        releases = self.root / 'releases'
        releases.mkdir()
        self.infra['controllers'] = {'agent': {'port': 43101, 'image_admission_command': [str(Path(sys.executable).resolve()), '-V'], 'egress_policy_ready': False},
            'provider': {'port': 43102, 'release_dir': str(releases)}}
        self.write_infra()
        result = self.prepare()
        config = load_config({'NEXUS_PERSONAL_CONFIG': str(self.destination / 'host.json')})
        self.assertEqual(result['controllers_configured'], ['agent', 'provider'])
        self.assertFalse(config['controllers']['agent']['egress_policy_ready'])
        self.assertEqual(len({config['secret_key'], config['encryption_key'], *[v['token'] for v in config['controllers'].values()]}), 4)
        self.assertIn('controller_admission_and_egress', result['unverified_steps'])

    def test_existing_destination_is_never_overwritten_or_repermissioned(self):
        self.destination.mkdir()
        sentinel = self.destination / 'keep.txt'
        sentinel.write_text('keep', encoding='utf-8')
        with patch.object(install, '_private_directory', side_effect=AssertionError('Must not touch permissions')):
            with self.assertRaisesRegex(install.InstallationError, 'INSTALL_DESTINATION_EXISTS'):
                self.prepare()
        self.assertEqual(sentinel.read_text(), 'keep')

    def test_builder_operator_config_is_preserved_without_claiming_verification(self):
        self.infra['controllers'] = {'agent': {'port': 43101,
            'image_admission_command': [str(Path(sys.executable).resolve()), '-V'], 'egress_policy_ready': True}}
        self.infra['python_builder'] = {'enabled': True, 'base_image': 'sha256:' + 'a' * 64,
            'isolation_ready': True, 'dependency_network': 'none'}
        self.write_infra()
        with patch('psycopg.connect', side_effect=AssertionError('No database access')):
            result = self.prepare()
        config = load_config({'NEXUS_PERSONAL_CONFIG': str(self.destination / 'host.json')})
        self.assertEqual(config['python_builder'], self.infra['python_builder'])
        self.assertTrue(result['python_builder_enabled'])
        self.assertFalse(result['services_started_by_command'])
        self.assertEqual(result['service_health'], 'not_checked')
        self.assertIn('controller_admission_and_egress', result['unverified_steps'])

    def test_invalid_foreign_or_tampered_bundle_has_no_destination_side_effects(self):
        for change in ('foreign', 'hash', 'path'):
            with self.subTest(change=change):
                original = json.loads(json.dumps(self.manifest))
                if change == 'foreign': self.manifest['distribution'] = 'enterprise'
                if change == 'hash': self.manifest['files']['index.html']['sha256'] = '0' * 64
                if change == 'path': self.manifest['files']['../outside'] = self.manifest['files']['index.html']
                self.write_manifest()
                with self.assertRaisesRegex(install.InstallationError, 'INSTALL_FRONTEND_INVALID'):
                    self.prepare()
                self.assertFalse(self.destination.exists())
                self.manifest = original

    def test_invalid_infrastructure_rejects_supplied_secrets_and_unknown_fields(self):
        for field, value in [('secret_key', 'forged-secret'), ('instance_id', 'forged'), ('controllers', {'agent': {'token': 'forged'}})]:
            with self.subTest(field=field):
                original = dict(self.infra)
                self.infra[field] = value
                self.write_infra()
                with self.assertRaisesRegex(install.InstallationError, 'INSTALL_INFRASTRUCTURE_INVALID'):
                    self.prepare()
                self.assertFalse(self.destination.exists())
                self.infra = original

    def test_failed_write_keeps_private_incomplete_directory_without_publishing_config(self):
        original = install._write
        def fail_asset(path, body):
            if path.name == 'main.js': raise OSError('secret-path-sentinel')
            return original(path, body)
        with patch.object(install, '_write', side_effect=fail_asset):
            with self.assertRaisesRegex(install.InstallationError, '^INSTALL_PREPARATION_FAILED$'):
                self.prepare()
        self.assertTrue((self.destination / 'installation-incomplete.json').exists())
        self.assertFalse((self.destination / 'host.json').exists())
        self.assertFalse((self.destination / 'installation.json').exists())
        with self.assertRaisesRegex(install.InstallationError, 'INSTALL_INCOMPLETE'):
            install.check(directory=self.destination)

    def test_permission_failure_writes_no_secret(self):
        with patch.object(install, '_private_directory', side_effect=OSError('private-acl-sentinel')):
            with self.assertRaisesRegex(install.InstallationError, '^INSTALL_PREPARATION_FAILED$'):
                self.prepare()
        self.assertFalse((self.destination / 'host.json').exists())

    def test_recheck_detects_config_and_asset_changes_instead_of_using_cached_bundle(self):
        self.prepare()
        path = self.destination / 'host.json'
        original = path.read_bytes()
        config = json.loads(original)
        config['public_origin'] = 'https://changed.example'
        path.write_text(json.dumps(config), encoding='utf-8')
        with self.assertRaisesRegex(install.InstallationError, 'INSTALL_CONFIGURATION_CHANGED'):
            install.check(directory=self.destination)
        path.write_bytes(original)
        (self.destination / 'web' / 'assets/main.js').write_bytes(b'changed')
        with self.assertRaisesRegex(install.InstallationError, 'INSTALL_FRONTEND_INVALID'):
            install.check(directory=self.destination)

    def test_valid_but_replaced_asset_manifest_is_detected(self):
        self.prepare()
        path = self.destination / 'web' / 'assets/main.js'
        path.write_bytes(b'changed')
        manifest_path = self.destination / 'web' / install.MANIFEST
        manifest = json.loads(manifest_path.read_bytes())
        manifest['files']['assets/main.js'] = {'size': 7, 'sha256': hashlib.sha256(b'changed').hexdigest()}
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(install.InstallationError, 'INSTALL_FRONTEND_INVALID'):
            install.check(directory=self.destination)

    def test_links_and_nonabsolute_destinations_are_rejected(self):
        for invalid in ('relative', self.root / '..' / 'escaped'):
            with self.assertRaisesRegex(install.InstallationError, 'INSTALL_PATH_INVALID'):
                self.prepare(directory=invalid)
        linked = self.root / 'linked'
        try:
            linked.symlink_to(self.bundle, target_is_directory=True)
        except OSError:
            self.skipTest('Platform did not allow creating the test symlink')
        with self.assertRaisesRegex(install.InstallationError, 'INSTALL_LINK_REJECTED'):
            self.prepare(web_bundle=linked)

    def test_cli_has_no_secret_flags_and_reports_redacted_errors(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = install.main(['prepare', '--directory', str(self.destination), '--origin', 'https://personal.example',
                '--infrastructure-file', str(self.infrastructure), '--web-bundle', str(self.bundle)])
        self.assertEqual(result, 0, err.getvalue())
        config = json.loads((self.destination / 'host.json').read_bytes())
        for secret in (config['secret_key'], config['encryption_key'], config['database']['password']):
            self.assertNotIn(secret, out.getvalue() + err.getvalue())
        self.assertNotIn(str(self.root), out.getvalue() + err.getvalue())
        self.assertEqual(json.loads(out.getvalue())['state'], 'prepared')
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), patch.object(install, '_bundle', side_effect=OSError('sensitive-error')):
            result = install.main(['prepare', '--directory', str(self.root/'error'), '--origin', 'https://personal.example',
                '--infrastructure-file', str(self.infrastructure), '--web-bundle', str(self.bundle)])
        self.assertEqual(result, 1)
        self.assertNotIn('sensitive-error', err.getvalue())
        self.assertNotIn(str(self.root), err.getvalue())

    def test_cli_cold_import_does_not_load_enterprise_or_test_host(self):
        code = '''
import importlib.abc,sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self,name,path=None,target=None):
        if any(name == p or name.startswith(p+'.') for p in ('nexus_enterprise','config','nexus_personal.tests','apps.iam','apps.billing','apps.tokenbank','apps.marketplace','apps.api_keys')):
            raise AssertionError('Private import attempted')
sys.meta_path.insert(0,Block())
from nexus_personal.install import main
main(['--help'])
'''
        result = subprocess.run([sys.executable, '-X', 'utf8', '-c', code], cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('prepare', result.stdout)

    def test_unknown_secret_argument_is_not_echoed_by_usage_errors(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as rejected:
            install.main(['check','--directory',str(self.destination),'--password','private-argv-sentinel'])
        self.assertEqual(rejected.exception.code,2)
        self.assertNotIn('private-argv-sentinel',err.getvalue())
        self.assertIn('PERSONAL_INSTALL_USAGE',err.getvalue())

    def test_invalid_tls_or_cloud_database_input_cannot_publish_configuration(self):
        for index, (origin, database) in enumerate([
                ('http://personal.example', self.config['database']),
                ('https://personal.example', {**self.config['database'], 'name': 'nexus_cloud'}),
                ('https://personal.example', {**self.config['database'], 'host': 'remote.example', 'sslmode': 'disable'})]):
            with self.subTest(index=index):
                self.infra['database'] = database
                self.write_infra()
                target = self.root / f'invalid-{index}'
                with self.assertRaisesRegex(install.InstallationError, 'INSTALL_PREPARATION_FAILED'):
                    self.prepare(directory=target, origin=origin)
                self.assertFalse((target/'host.json').exists())
                self.assertFalse((target/'installation.json').exists())

    def test_oversized_or_nonregular_input_is_rejected_before_opening(self):
        self.infrastructure.write_bytes(b'x' * 32769)
        with self.assertRaisesRegex(install.InstallationError, 'INSTALL_INFRASTRUCTURE_INVALID'):
            self.prepare()
        with self.assertRaisesRegex(install.InstallationError, 'INSTALL_INFRASTRUCTURE_INVALID'):
            self.prepare(infrastructure_file=self.root)
        self.assertFalse(self.destination.exists())

    def test_broadened_installed_directory_permissions_are_rejected(self):
        self.prepare()
        if os.name == 'nt':
            script = r'''
$ErrorActionPreference='Stop'
$p=$env:NEXUS_INSTALL_PERMISSION_FIXTURE
$a=[System.IO.Directory]::GetAccessControl($p)
$sid=[System.Security.Principal.SecurityIdentifier]::new('S-1-1-0')
$a.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new($sid,'Read','None','None','Allow'))
[System.IO.Directory]::SetAccessControl($p,$a)
'''
            subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
                env={**os.environ,'NEXUS_INSTALL_PERMISSION_FIXTURE':str(self.destination/'keys')},
                check=True,capture_output=True,timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        else:
            (self.destination/'keys').chmod(0o755)
        with self.assertRaisesRegex(install.InstallationError, 'INSTALL_PERMISSIONS_FAILED'):
            install.check(directory=self.destination)

    def test_config_readback_failure_never_marks_partial_installation_as_prepared(self):
        from django.core.exceptions import ImproperlyConfigured
        with patch.object(install,'load_config',side_effect=ImproperlyConfigured('private-sentinel')):
            with self.assertRaisesRegex(install.InstallationError,'INSTALL_PREPARATION_FAILED'):
                self.prepare()
        self.assertTrue((self.destination/'host.json').is_file())
        self.assertTrue((self.destination/'installation-incomplete.json').is_file())
        self.assertFalse((self.destination/'installation.json').exists())
        with self.assertRaisesRegex(install.InstallationError,'INSTALL_INCOMPLETE'):
            install.check(directory=self.destination)

    def test_unprotected_infrastructure_credentials_are_rejected(self):
        if os.name == 'nt':
            script = r'''
$ErrorActionPreference='Stop'
$p=$env:NEXUS_INSTALL_INPUT_FIXTURE
$a=[System.IO.File]::GetAccessControl($p)
$sid=[System.Security.Principal.SecurityIdentifier]::new('S-1-1-0')
$a.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new($sid,'Read','Allow'))
[System.IO.File]::SetAccessControl($p,$a)
'''
            subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
                env={**os.environ,'NEXUS_INSTALL_INPUT_FIXTURE':str(self.infrastructure)},check=True,
                capture_output=True,timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        else:
            self.infrastructure.chmod(0o644)
        with self.assertRaisesRegex(install.InstallationError,'INSTALL_PREPARATION_FAILED'):
            self.prepare()
        self.assertFalse(self.destination.exists())


@unittest.skipUnless(os.environ.get('NEXUS_PERSONAL_TEST_WEB_BUNDLE'), 'Explicit built Community frontend required')
class PersonalBuiltFrontendInstallTests(unittest.TestCase):
    setUp = PersonalInstallationPreparationTests.setUp
    write_infra = PersonalInstallationPreparationTests.write_infra
    write_manifest = PersonalInstallationPreparationTests.write_manifest
    prepare = PersonalInstallationPreparationTests.prepare

    def test_actual_built_bundle_installs_and_serves_through_production_middleware(self):
        result = self.prepare(web_bundle=Path(os.environ['NEXUS_PERSONAL_TEST_WEB_BUNDLE']))
        self.assertGreater(result['asset_count'], 10)
        code = '''
import importlib.abc,sys,os
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self,name,path=None,target=None):
        if any(name == p or name.startswith(p+'.') for p in ('nexus_enterprise','config','nexus_personal.tests','apps.iam','apps.billing','apps.tokenbank','apps.marketplace','apps.api_keys')):
            raise AssertionError('Private import attempted')
sys.meta_path.insert(0,Block())
os.environ['DJANGO_SETTINGS_MODULE']='nexus_personal.settings'
from nexus_personal.asgi import application
from nexus_personal.frontend import PersonalFrontendMiddleware
from django.test import RequestFactory
def missing(request): raise AssertionError('Frontend was not installed')
response=PersonalFrontendMiddleware(missing)(RequestFactory().get('/',secure=True,HTTP_HOST='personal.example:9443'))
assert response.status_code == 200
assert b'<div id="root">' in response.content
assert b'/static/web/assets/' in response.content
print('prepared-production-frontend-ok')
'''
        probe = subprocess.run([sys.executable,'-X','utf8','-c',code],cwd=Path(__file__).resolve().parents[1],
            env={**os.environ,'NEXUS_PERSONAL_CONFIG':str(self.destination/'host.json')},
            capture_output=True,text=True, encoding='utf-8',timeout=45)
        self.assertEqual(probe.returncode,0,probe.stderr)
        self.assertEqual(probe.stdout.strip(),'prepared-production-frontend-ok')


if __name__ == '__main__':
    unittest.main()
