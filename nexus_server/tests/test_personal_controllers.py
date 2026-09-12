"""Personal configuration, real loopback/auth protocol, and guarded launch.

Protocol probes use the actual ping dispatcher, never a fake Docker runner.
They do not claim Docker starts or an image scanner/egress policy is installed.
"""
import copy
import json
from pathlib import Path
import secrets
import socket
import sys
import unittest
from django.core.exceptions import ImproperlyConfigured
from nexus_personal.host_config import validate_config
from tests import test_personal_host as fixtures


DENY_PRIVATE = '''
import importlib.abc, sys
class BlockPrivate(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p + '.') for p in (
            'config', 'nexus_enterprise', 'nexus_personal.tests', 'apps.iam',
            'apps.billing', 'apps.tokenbank', 'apps.marketplace', 'apps.api_keys')):
            raise ModuleNotFoundError('Forbidden controller dependency: ' + fullname)
sys.meta_path.insert(0, BlockPrivate())
'''


class PersonalControllerTests(unittest.TestCase):
    setUp = fixtures.PersonalHostTests.setUp

    def probe(self, source, environment=None):
        result = fixtures.PersonalHostTests.probe(self, DENY_PRIVATE + source, environment)
        for fields in self.config.get('controllers', {}).values():
            self.assertNotIn(fields['token'], result.stdout + result.stderr)
        return result

    def configure(self):
        # Hold both reservations until distinct ports are selected. If a port
        # becomes occupied before the probe binds, it must fail, never take over.
        with socket.socket() as first, socket.socket() as second:
            first.bind(('127.0.0.1', 0))
            second.bind(('127.0.0.1', 0))
            ports = [first.getsockname()[1], second.getsockname()[1]]
        releases = self.state / 'provider-releases'
        releases.mkdir()
        self.config['controllers'] = {
            'agent': {'port': ports[0], 'token': secrets.token_urlsafe(48),
                'image_admission_command': [str(Path(sys.executable).resolve())], 'egress_policy_ready': False},
            'provider': {'port': ports[1], 'token': secrets.token_urlsafe(48), 'release_dir': str(releases)},
        }
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.config), encoding='utf-8')

    def test_old_config_stays_unconfigured_and_provider_can_be_enabled_independently(self):
        self.assertEqual(validate_config(self.config)['controllers'], {})
        result = self.probe('from nexus_personal.processes import main; main(["provider-controller"])')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('PERSONAL_CONTROLLER_UNCONFIGURED', result.stderr)
        self.configure()
        del self.config['controllers']['agent']
        self.save()
        result = self.probe('''
from django.conf import settings
assert settings.NEXUS_PERSONAL_CONFIGURED_CONTROLLERS == ('provider',)
assert not settings.NEXUS_AGENT_RUNTIME_CONTROLLER_TOKEN
assert not settings.NEXUS_AGENT_IMAGE_ADMISSION_COMMAND
assert not settings.NEXUS_AGENT_RUNTIME_EGRESS_POLICY_READY
assert settings.NEXUS_PROVIDER_RUNTIME_CONTROLLER_TOKEN
assert settings.NEXUS_PROVIDER_RUNTIME_RELEASE_DIR
assert settings.NEXUS_PROVIDER_RUNTIME_REQUIRE_VERIFIED_RELEASE
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_python_builder_defaults_stay_disabled_despite_environment(self):
        self.assertEqual(validate_config(self.config)['python_builder'], {
            'enabled': False, 'base_image': '', 'isolation_ready': False, 'dependency_network': 'none'})
        result = self.probe('''
from django.conf import settings
assert not settings.NEXUS_AGENT_PYTHON_BUILDS_ENABLED
assert not settings.NEXUS_AGENT_PYTHON_ISOLATION_READY
assert settings.NEXUS_AGENT_PYTHON_BASE_IMAGE == ''
assert settings.NEXUS_AGENT_PYTHON_DEPENDENCY_NETWORK == 'none'
''', {'NEXUS_AGENT_PYTHON_BUILDS_ENABLED': '1', 'NEXUS_AGENT_PYTHON_ISOLATION_READY': '1',
      'NEXUS_AGENT_PYTHON_BASE_IMAGE': 'untrusted:latest', 'NEXUS_AGENT_PYTHON_DEPENDENCY_NETWORK': 'host'})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_python_builder_protected_config_reaches_production_settings(self):
        self.configure()
        self.config['controllers']['agent']['egress_policy_ready'] = True
        self.config['python_builder'] = {'enabled': True, 'base_image': 'sha256:' + 'a' * 64,
            'isolation_ready': True, 'dependency_network': 'nexus-build-dependencies'}
        self.save()
        result = self.probe('''
from django.conf import settings
assert settings.NEXUS_PRODUCTION
assert settings.NEXUS_AGENT_PYTHON_BUILDS_ENABLED
assert settings.NEXUS_AGENT_PYTHON_ISOLATION_READY
assert settings.NEXUS_AGENT_PYTHON_BASE_IMAGE == 'sha256:' + 'a' * 64
assert settings.NEXUS_AGENT_PYTHON_DEPENDENCY_NETWORK == 'nexus-build-dependencies'
# Config propagation does not make a missing profile, Docker, or isolation
# acceptable. Prove the existing execution guard still runs before Docker.
import django
django.setup()
from django.test import override_settings
from types import SimpleNamespace
from unittest.mock import patch
from apps.agents.python_builder import build_image, BuildFailure
with override_settings(NEXUS_AGENT_PYTHON_ISOLATION_READY=False), patch('apps.agents.python_builder.docker') as docker:
    try:
        build_image(SimpleNamespace(base_image=settings.NEXUS_AGENT_PYTHON_BASE_IMAGE), lambda _: None)
    except BuildFailure as error:
        assert error.code == 'BUILD_ISOLATION_REQUIRED'
    else:
        raise AssertionError('Isolation guard bypassed')
    docker.assert_not_called()
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_python_builder_requires_operator_isolation_and_controller_policy(self):
        builder = {'enabled': True, 'base_image': 'sha256:' + 'a' * 64,
                   'isolation_ready': True, 'dependency_network': 'none'}
        with self.assertRaises(ImproperlyConfigured):
            validate_config({**self.config, 'python_builder': builder})
        self.configure()
        with self.assertRaises(ImproperlyConfigured):
            validate_config({**self.config, 'python_builder': builder})
        self.config['controllers']['agent']['egress_policy_ready'] = True
        with self.assertRaises(ImproperlyConfigured):
            validate_config({**self.config, 'python_builder': {**builder, 'isolation_ready': False}})
        self.assertEqual(validate_config({**self.config, 'python_builder': builder})['python_builder'], builder)
        paused = {**builder, 'enabled': False, 'isolation_ready': False}
        self.assertEqual(validate_config({**self.config, 'python_builder': paused})['python_builder'], paused)

    def test_python_builder_rejects_mutable_profiles_unsafe_networks_and_extra_options(self):
        self.configure()
        self.config['controllers']['agent']['egress_policy_ready'] = True
        builder = {'enabled': True, 'base_image': 'sha256:' + 'a' * 64,
                   'isolation_ready': True, 'dependency_network': 'none'}
        changes = [{'enabled': 1}, {'enabled': 'true'}, {'isolation_ready': 1},
                   {'docker': '/untrusted/docker'}, {'extra': 'private-value-sentinel'}]
        changes += [{'base_image': v} for v in ('profile:latest', '', 'sha256:bad',
                    'registry/user:private-value-sentinel@sha256:' + 'a' * 64, None)]
        changes += [{'dependency_network': v} for v in ('host', 'bridge', 'default',
                    'container:another', '--privileged', 'bad\nname', '', 'x' * 65, None)]
        for change in changes:
            with self.subTest(field=next(iter(change))), self.assertRaises(ImproperlyConfigured) as error:
                validate_config({**self.config, 'python_builder': {**builder, **change}})
            self.assertNotIn('private-value-sentinel', str(error.exception))
        for value in (None, [], {'enabled': True}):
            with self.assertRaises(ImproperlyConfigured):
                validate_config({**self.config, 'python_builder': value})

    def test_tokens_ports_and_network_overrides_are_strict_and_errors_are_redacted(self):
        self.configure()
        invalid = [None, [], {'foreign': {}}, {'provider': {}},
            {'provider': {**self.config['controllers']['provider'], 'endpoint': 'tcp://0.0.0.0:9000'}}]
        for port in (True, 0, 1023, 65536, '19001'):
            invalid.append({'provider': {**self.config['controllers']['provider'], 'port': port}})
        for token in ('short-secret', 'a' * 64, self.config['secret_key'], self.config['encryption_key'], 'x\n' * 32):
            invalid.append({'provider': {**self.config['controllers']['provider'], 'token': token}})
        for field in ('token', 'port'):
            duplicate = copy.deepcopy(self.config['controllers'])
            duplicate['provider'][field] = duplicate['agent'][field]
            invalid.append(duplicate)
        for controllers in invalid:
            with self.subTest(case=type(controllers).__name__), self.assertRaises(ImproperlyConfigured) as error:
                validate_config({**self.config, 'controllers': controllers})
            self.assertNotIn(self.config['controllers']['provider']['token'], str(error.exception))

    def test_agent_requires_explicit_admission_arguments_and_egress_state(self):
        self.configure()
        for command in ('echo pass', [], ['relative'], [sys.executable, 'a\nb'], [sys.executable] * 33, [None]):
            fields = {**self.config['controllers']['agent'], 'image_admission_command': command}
            with self.subTest(command_type=type(command).__name__), self.assertRaises(ImproperlyConfigured):
                validate_config({**self.config, 'controllers': {'agent': fields}})
        for ready in ('true', 1, None):
            fields = {**self.config['controllers']['agent'], 'egress_policy_ready': ready}
            with self.subTest(ready=ready), self.assertRaises(ImproperlyConfigured):
                validate_config({**self.config, 'controllers': {'agent': fields}})
        result = self.probe('from nexus_personal.processes import main; main(["agent-controller"])')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('AGENT_EGRESS_POLICY_REQUIRED', result.stderr)

    def test_release_directory_and_verifier_cannot_be_workload_controlled(self):
        self.configure()
        storage = self.state / 'storage'
        storage.mkdir()
        executable = storage / 'untrusted-verifier'
        executable.write_text('untrusted workload fixture', encoding='utf-8')
        fields = {**self.config['controllers']['agent'], 'image_admission_command': [str(executable)]}
        with self.assertRaises(ImproperlyConfigured):
            validate_config({**self.config, 'controllers': {'agent': fields}})
        for directory in ('relative', str(self.state), str(storage), str(executable), None):
            fields = {**self.config['controllers']['provider'], 'release_dir': directory}
            with self.subTest(directory_type=type(directory).__name__), self.assertRaises(ImproperlyConfigured):
                validate_config({**self.config, 'controllers': {'provider': fields}})

    def test_missing_provider_release_receipt_fails_closed_without_cloud_fallback(self):
        self.configure()
        result = self.probe('''
import django
django.setup()
from apps.providers.runtime_release import approved_release
from rest_framework.exceptions import APIException
for kind in ('codex_proxy', 'cliproxyapi'):
    try:
        approved_release(kind)
    except APIException as error:
        assert 'approval is missing or invalid' in str(error)
    else:
        raise AssertionError('Unapproved runtime image accepted')
''', {'NEXUS_PROVIDER_RUNTIME_RELEASE_DIR': 'ignored-cloud-release-directory'})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_launch_uses_real_commands_correct_role_and_does_not_load_cloud(self):
        self.configure()
        self.config['controllers']['agent']['egress_policy_ready'] = True
        self.save()
        for kind in ('agent', 'provider'):
            result = self.probe('''
from unittest.mock import patch
from django.conf import settings
from nexus_personal.processes import main
kind = ''' + repr(kind) + '''
def dispatch(argv):
    assert argv == ['personal-manage', f'run_{kind}_runtime_controller']
    assert settings.SETTINGS_MODULE == 'nexus_personal.settings'
    assert settings.NEXUS_PROCESS_ROLE == kind + '-controller'
    assert settings.NEXUS_AGENT_RUNTIME_CONTROLLER_TOKEN != settings.NEXUS_PROVIDER_RUNTIME_CONTROLLER_TOKEN
    assert getattr(settings, f'NEXUS_{kind.upper()}_RUNTIME_CONTROLLER_SOCKET').startswith('tcp://127.0.0.1:')
    print('controller-launch-checked')
with patch('django.core.management.execute_from_command_line', dispatch):
    main([kind + '-controller'])
''', {'NEXUS_PROCESS_ROLE': 'web', 'NEXUS_AGENT_RUNTIME_CONTROLLER_TOKEN': 'ignored-cloud-token',
       'NEXUS_PROVIDER_RUNTIME_CONTROLLER_SOCKET': 'tcp://0.0.0.0:9'})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('controller-launch-checked', result.stdout)

    def test_controller_cli_rejects_worker_flags_and_wrong_role(self):
        self.configure()
        for args in (['provider-controller', '--once'], ['agent-controller', '--concurrency', '2']):
            result = self.probe('from nexus_personal.processes import main; main(' + repr(args) + ')')
            self.assertNotEqual(result.returncode, 0)
        result = self.probe('from nexus_personal.controllers import run_controller; run_controller("provider")',
                            {'NEXUS_PROCESS_ROLE': 'web'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('dedicated process', result.stderr)

    def test_python_builder_uses_dedicated_role_and_management_command(self):
        self.configure()
        self.config['controllers']['agent']['egress_policy_ready'] = True
        self.config['python_builder'] = {'enabled': True, 'base_image': 'sha256:' + 'a' * 64,
            'isolation_ready': True, 'dependency_network': 'none'}
        self.save()
        result = self.probe('''
from unittest.mock import patch
from django.conf import settings
from nexus_personal.processes import main
def dispatch():
    assert settings.NEXUS_PROCESS_ROLE == 'agent-builder'
    assert __import__('sys').argv == ['personal-manage', 'run_agent_python_builds', '--once']
with patch('nexus_personal.manage.main', dispatch):
    main(['python-builder', '--once'])
print('python-builder-launch-checked')
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('python-builder-launch-checked', result.stdout)

    def test_real_controller_sockets_isolate_tokens_and_refuse_takeover(self):
        self.configure()
        result = self.probe('''
import os, socket, threading, time
import django
django.setup()
from django.conf import settings
from apps.agents.runtime_controller import AgentRuntimeControllerServer, AgentRuntimeControllerClient, AgentRuntimeControllerError
from apps.providers.provider_controller import ProviderRuntimeControllerServer, ProviderRuntimeControllerClient, ProviderRuntimeControllerError
entries = [('agent', AgentRuntimeControllerServer, AgentRuntimeControllerClient, AgentRuntimeControllerError),
           ('provider', ProviderRuntimeControllerServer, ProviderRuntimeControllerClient, ProviderRuntimeControllerError)]
running = []
errors = []
def serve(server):
    try:
        server.serve_forever()
    except Exception as error:
        errors.append((type(error).__name__, getattr(error, 'errno', None)))
try:
    for kind, Server, Client, Error in entries:
        server = Server()  # Default real dispatcher, no injected execution runner.
        thread = threading.Thread(target=serve, args=(server,), daemon=True)
        running.append((server, thread))
        thread.start()
        client = Client()
        deadline = time.monotonic() + 5
        while True:
            try:
                assert client.ping() == {'status': 'ready', 'protocol_version': 1}
                break
            except Error:
                assert not errors and time.monotonic() < deadline, 'Controller failed to bind'
                time.sleep(.02)
        other = 'provider' if kind == 'agent' else 'agent'
        for token in ('wrong', '', getattr(settings, f'NEXUS_{other.upper()}_RUNTIME_CONTROLLER_TOKEN')):
            try:
                Client(token=token).ping()
            except Error as error:
                assert 'authentication failed' in str(error)
            else:
                raise AssertionError('Unauthenticated controller accepted')
        try:
            client.request(action='not-allowed')
        except Error as error:
            assert 'Unsupported' in str(error)
        else:
            raise AssertionError('Unknown action accepted')
        endpoint = getattr(settings, f'NEXUS_{kind.upper()}_RUNTIME_CONTROLLER_SOCKET')
        with socket.socket() as duplicate:
            if os.name == 'posix':
                duplicate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                duplicate.bind(('127.0.0.1', int(endpoint.rsplit(':', 1)[1])))
            except OSError:
                pass
            else:
                raise AssertionError('Live controller endpoint was taken over')
        assert client.ping()['status'] == 'ready'
    # A stopped controller cannot make the other service unavailable. Rebind
    # the exact same endpoint and reuse the existing client configuration.
    for index, (kind, Server, Client, Error) in enumerate(entries):
        server, thread = running[index]
        server.stop()
        thread.join(timeout=4)
        assert not thread.is_alive()
        assert entries[1-index][2]().ping()['status'] == 'ready'
        try:
            Client().ping()
        except Error:
            pass
        else:
            raise AssertionError('Stopped controller remained reachable')
        replacement = Server()
        replacement_thread = threading.Thread(target=serve, args=(replacement,), daemon=True)
        running.append((replacement, replacement_thread))
        replacement_thread.start()
        deadline = time.monotonic() + 5
        while True:
            try:
                assert Client().ping()['status'] == 'ready'
                break
            except Error:
                assert not errors and time.monotonic() < deadline, ('Controller restart could not bind', kind, errors)
                time.sleep(.02)
finally:
    for server, thread in running:
        server.stop()
    for server, thread in running:
        thread.join(timeout=4)
        assert not thread.is_alive(), 'Controller did not stop'
assert not errors, errors
print('real-personal-controller-auth-ok')
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('real-personal-controller-auth-ok', result.stdout)
