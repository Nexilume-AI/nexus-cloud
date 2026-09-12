"""Production settings boot with no Enterprise or test-host imports.

These checks do not claim installed controllers, a worker fleet or a public UI.
"""
import copy
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from nexus_personal.host_config import load_config, validate_config


class PersonalHostTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="nexus-personal-host-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        if os.name == 'nt':
            # This fixture owns only its newly created temporary directory.
            # Remove inherited broad access before any secret is written.
            script = r"""
$ErrorActionPreference = 'Stop'
$path = $env:NEXUS_HOST_TEST_DIRECTORY
$acl = [System.IO.Directory]::GetAccessControl($path)
$acl.SetAccessRuleProtection($true, $false)
foreach ($rule in @($acl.Access)) { [void]$acl.RemoveAccessRuleSpecific($rule) }
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$rule = [System.Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
$acl.AddAccessRule($rule)
[System.IO.Directory]::SetAccessControl($path, $acl)
"""
            subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                env={**os.environ, 'NEXUS_HOST_TEST_DIRECTORY': str(self.root)},
                check=True, capture_output=True, timeout=10,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        self.config = {
            "schema_version": 1, "instance_id": str(uuid4()), "public_origin": "https://personal.example:9443/",
            "state_dir": str(self.state), "secret_key": secrets.token_urlsafe(64),
            "encryption_key": Fernet.generate_key().decode(),
            "database": {"name": "nexus_personal_probe", "host": "127.0.0.1", "port": 5432,
                "user": "not-connected", "password": "not-connected", "sslmode": "disable"},
            "redis_url": "redis://127.0.0.1:6379/12",
        }
        self.path = self.root / "host.json"
        self.path.write_text(json.dumps(self.config), encoding="utf-8")
        self.path.chmod(0o600)

    def probe(self, source, environment=None):
        env = {**os.environ, "NEXUS_PERSONAL_CONFIG": str(self.path), "DJANGO_SETTINGS_MODULE": "nexus_personal.settings",
            "DATABASE_URL": "postgresql://ignored:ignored@127.0.0.1/enterprise_must_not_be_used",
            **(environment or {})}
        result = subprocess.run([sys.executable, '-X', 'utf8', '-c', source], env=env,
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, encoding='utf-8', timeout=45)
        self.assertNotIn(self.config['secret_key'], result.stdout + result.stderr)
        self.assertNotIn(self.config['encryption_key'], result.stdout + result.stderr)
        return result

    def test_protected_config_reads_without_cloud_environment_fallback(self):
        result = load_config({"NEXUS_PERSONAL_CONFIG": str(self.path)})
        self.assertEqual(result['public_origin'], 'https://personal.example:9443')
        self.assertEqual(result['database']['name'], 'nexus_personal_probe')
        with self.assertRaises(ImproperlyConfigured):
            load_config({"DATABASE_URL": "postgresql://existing-cloud"})

    def test_probe_preserves_unicode_output_without_inherited_utf8_mode(self):
        result = self.probe(
            "import sys; print('中文输出'); print('中文错误', file=sys.stderr)",
            environment={"PYTHONUTF8": "0"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "中文输出")
        self.assertEqual(result.stderr.strip(), "中文错误")

    def test_monitoring_smtp_and_retention_are_explicit_protected_configuration(self):
        default = validate_config(self.config)["monitoring"]
        self.assertEqual(default, {"smtp": None, "retention_enabled": False, "retention_days": 90})
        smtp = {"host": "smtp.example.test", "port": 587, "username": "owner", "password": "test-smtp-secret",
                "tls": "starttls", "from_email": "nexus@example.test"}
        self.config["monitoring"] = {"smtp": smtp, "retention_enabled": True, "retention_days": 45}
        value = validate_config(self.config)
        self.assertEqual(value["monitoring"]["smtp"], smtp)
        self.assertIsNot(value["monitoring"]["smtp"], smtp)
        self.path.write_text(json.dumps(self.config), encoding="utf-8")
        result = self.probe('''
from django.conf import settings
assert settings.NEXUS_MONITOR_SMTP_CONFIGURED is True
assert settings.EMAIL_HOST == 'smtp.example.test'
assert settings.EMAIL_PORT == 587 and settings.EMAIL_USE_TLS and not settings.EMAIL_USE_SSL
assert settings.EMAIL_TIMEOUT == 15
assert settings.NEXUS_MONITOR_RETENTION_ENABLED and settings.NEXUS_MONITOR_RETENTION_DAYS == 45
print('personal-monitoring-config-ok')
''', environment={"EMAIL_HOST": "ignored.enterprise.invalid", "EMAIL_HOST_PASSWORD": "ignored-secret"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "personal-monitoring-config-ok")
        self.assertNotIn(smtp["password"], result.stdout + result.stderr)

    def test_monitoring_invalid_fields_fail_without_echoing_credentials(self):
        smtp = {"host": "smtp.example.test", "port": 587, "username": "owner", "password": "test-smtp-secret",
                "tls": "starttls", "from_email": "nexus@example.test"}
        cases = [{"retention_days": 29}, {"retention_days": True}, {"retention_enabled": "yes"},
                 {"unknown": "test-smtp-secret"}, {"smtp": "test-smtp-secret"}]
        for override in ({"host": "https://user:secret@host"}, {"port": True}, {"port": 0}, {"tls": "none"},
                         {"tls": "verify-disabled"}, {"username": ""}, {"from_email": "bad\r\nBcc: injected"}):
            cases.append({"smtp": {**smtp, **override}})
        for monitoring in cases:
            with self.subTest(fields=list(monitoring)):
                with self.assertRaises(ImproperlyConfigured) as error:
                    validate_config({**self.config, "monitoring": monitoring})
                self.assertNotIn("test-smtp-secret", str(error.exception))
                self.assertNotIn("user:secret", str(error.exception))
        local = {**smtp, "host": "127.0.0.1", "tls": "none", "username": "", "password": ""}
        self.assertEqual(validate_config({**self.config, "monitoring": {"smtp": local}})["monitoring"]["smtp"], local)

    def test_production_frontend_stack_works_before_database_without_cloud_assets(self):
        result = self.probe('''
import importlib.abc, sys
class BlockPrivate(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p + '.') for p in (
            'config', 'nexus_enterprise', 'nexus_personal.tests', 'apps.iam',
            'apps.billing', 'apps.tokenbank', 'apps.marketplace', 'apps.api_keys')):
            raise ModuleNotFoundError('Forbidden production dependency: ' + fullname)
sys.meta_path.insert(0, BlockPrivate())
from nexus_personal.asgi import application
from django.conf import settings
from django.test import Client
from pathlib import Path
import hashlib, json
web = Path(settings.NEXUS_WEB_INDEX_PATH).parent
web.mkdir()
body = b'<html>Independent Community shell</html>'
(web / 'index.html').write_bytes(body)
(web / 'community-assets.json').write_text(json.dumps({
    'schema_version': 1, 'distribution': 'community', 'files': {
        'index.html': {'size': len(body), 'sha256': hashlib.sha256(body).hexdigest()}}}))
client = Client(HTTP_HOST='personal.example:9443')
response = client.get('/login', secure=True)
assert response.status_code == 200, response.status_code
assert response.content == body
assert response.cookies['csrftoken']['secure']
assert response['Cache-Control'] == 'no-store'
assert response['Strict-Transport-Security'] == 'max-age=31536000'
assert client.get('/login', secure=False).status_code == 301
assert client.get('/login', secure=True, HTTP_HOST='wrong.invalid').status_code == 400
assert client.get('/static/web/missing.js', secure=True).status_code == 404
from django.urls import resolve
assert resolve('/api/v1/personal/context/').url_name == 'personal-context'
print('personal-production-frontend-ok')
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'personal-production-frontend-ok')

    def test_unexpected_api_error_never_logs_exception_or_request_secrets(self):
        result = self.probe('''
import logging
from django.conf import settings
import django
django.setup()
from rest_framework.views import APIView
from rest_framework.test import APIRequestFactory
from rest_framework.exceptions import ValidationError
records = []
class Capture(logging.Handler):
    def emit(self, record): records.append(record)
capture = Capture()
root = logging.getLogger()
previous = list(root.handlers)
root.handlers = [capture]
secret = 'upstream-private-marker-do-not-log'
class Broken(APIView):
    authentication_classes = []
    permission_classes = []
    def get(self, request):
        try: raise ValueError('inner ' + secret)
        except ValueError as inner: raise RuntimeError('Bearer ' + secret) from inner
request = APIRequestFactory().get('/probe?credential=' + secret, HTTP_AUTHORIZATION='Bearer ' + secret)
request.request_id = secret
try:
    response = Broken.as_view()(request)
    assert response.status_code == 500
    assert response.data['error']['code'] == 'INTERNAL_SERVER_ERROR'
    assert response.data['error']['message'] == 'Internal server error.'
    assert records, 'unhandled error must remain observable'
    assert all(record.exc_info is None and record.stack_info is None for record in records), 'unsafe traceback retained'
    assert all(secret not in logging.Formatter().format(record) for record in records), 'unsafe exception message logged'
    assert all(secret not in str(record.__dict__) for record in records), 'unsafe structured log metadata retained'
    class Invalid(APIView):
        authentication_classes = []
        permission_classes = []
        def get(self, request): raise ValidationError({'name': 'Name is required.'})
    response = Invalid.as_view()(APIRequestFactory().get('/probe'))
    assert response.status_code == 400
    assert response.data['error']['code'] == 'VALIDATION_ERROR'
    assert 'name' in response.data['error']['message']
finally: root.handlers = previous
print('personal-error-log-safe')
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'personal-error-log-safe')

    def test_invalid_values_never_echo_secret_inputs(self):
        cases = [
            {"schema_version": True},
            {"public_origin": "http://personal.example"}, {"public_origin": "https://user:secret@personal.example"},
            {"public_origin": "https://personal.example/path?token=secret"}, {"public_origin": "https://*.example"},
            {"secret_key": "dev-only-secret"}, {"encryption_key": "private-secret-invalid"},
            {"redis_url": "redis://redis.internal/2"}, {"redis_url": "redis://localhost/0"},
            {"state_dir": str(Path(__file__).resolve().parents[1])},
            {"state_dir": str(Path.home())}, {"state_dir": "relative"}, {"instance_id": "not-a-uuid"},
            {"unexpected": "private-secret-invalid"},
        ]
        for update in cases:
            with self.subTest(keys=list(update)):
                with self.assertRaises(ImproperlyConfigured) as error:
                    validate_config({**self.config, **update})
                self.assertNotIn('private-secret-invalid', str(error.exception))
                self.assertNotIn(self.config['secret_key'], str(error.exception))

    def test_database_requires_a_dedicated_postgres_identity_and_remote_tls(self):
        for update in ({'name': 'nexus_cloud'}, {'name': 'postgres'}, {'port': True},
                       {'host': 'db.internal', 'sslmode': 'require'}, {'password': ''}):
            config = copy.deepcopy(self.config)
            config['database'].update(update)
            with self.subTest(update=update), self.assertRaises(ImproperlyConfigured):
                validate_config(config)

    def test_malformed_and_oversized_files_fail_without_contents(self):
        for content in ('{"secret_key": "do-not-echo"', 'do-not-echo' * 4000):
            self.path.write_text(content, encoding='utf-8')
            with self.assertRaises(ImproperlyConfigured) as error:
                load_config({'NEXUS_PERSONAL_CONFIG': str(self.path)})
            self.assertNotIn('do-not-echo', str(error.exception))

    @unittest.skipIf(os.name == 'nt', 'POSIX ownership bits; Windows uses DACL validation')
    def test_posix_group_readable_config_is_rejected(self):
        self.path.chmod(0o640)
        with self.assertRaises(ImproperlyConfigured):
            load_config({'NEXUS_PERSONAL_CONFIG': str(self.path)})

    def test_acl_checker_fails_closed(self):
        from nexus_personal.host_config import _windows_private_acl
        with patch('nexus_personal.host_config.subprocess.run', side_effect=OSError('private-path')):
            with self.assertRaises(ImproperlyConfigured) as error:
                _windows_private_acl(self.path)
            self.assertNotIn('private-path', str(error.exception))

    def test_cold_production_asgi_and_schema_block_test_and_private_modules(self):
        result = self.probe('''
import importlib.abc, sys
class BlockPrivate(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p + '.') for p in (
            'config', 'nexus_enterprise', 'nexus_personal.tests', 'apps.iam',
            'apps.billing', 'apps.tokenbank', 'apps.marketplace', 'apps.api_keys')):
            raise ModuleNotFoundError('Forbidden production host dependency: ' + fullname)
sys.meta_path.insert(0, BlockPrivate())
from nexus_personal.asgi import application
from django.conf import settings
from django.core.checks import run_checks
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.state import ProjectState
from django.apps import apps
assert not run_checks(), run_checks()
loader = MigrationLoader(None)
assert not MigrationAutodetector(loader.project_state(), ProjectState.from_apps(apps)).changes(graph=loader.graph)
assert not settings.DEBUG and settings.NEXUS_PRODUCTION
assert settings.REST_FRAMEWORK['EXCEPTION_HANDLER'] == 'nexus_personal.exceptions.personal_exception_handler'
from rest_framework.settings import api_settings
from apps.providers.connection_services import ProviderMustBeStopped
from types import SimpleNamespace
error = api_settings.EXCEPTION_HANDLER(ProviderMustBeStopped(), {'request': SimpleNamespace(request_id='host-error-probe')})
assert error.status_code == 409 and error.data['ok'] is False
assert error.data['error']['code'] == 'PROVIDER_MUST_BE_STOPPED'
assert error.data['request_id'] == 'host-error-probe'
from nexus_personal.tool_setup import PersonalToolSetupUnavailable
error = api_settings.EXCEPTION_HANDLER(PersonalToolSetupUnavailable(), {'request': SimpleNamespace(request_id='host-tool-error')})
assert error.status_code == 400 and error.data['ok'] is False
assert error.data['error']['code'] == 'PERSONAL_TOOL_SETUP_APPLY_UNAVAILABLE'
assert error.data['request_id'] == 'host-tool-error'
assert settings.DATABASES['default']['NAME'] == 'nexus_personal_probe'
assert settings.NEXUS_AGENT_RUNTIME_RUNNER == 'controller'
assert not settings.NEXUS_LEGACY_SSH_ENABLED
assert not settings.CELERY_TASK_ALWAYS_EAGER
assert settings.SESSION_COOKIE_SECURE and settings.CSRF_COOKIE_SECURE
assert settings.NEXUS_GATEWAY_MOCK_HOSTS == ()
assert settings.NEXUS_EDGE_REQUIRE_MTLS_HEADER
assert settings.NEXUS_EDGE_JWT_ISSUER == settings.NEXUS_PUBLIC_BASE_URL + '/edge'
assert 'nexus-cloud' not in settings.NEXUS_EDGE_JWT_PRIVATE_KEY_FILE
assert 'django.middleware.csrf.CsrfViewMiddleware' in settings.MIDDLEWARE
from celery import current_app
assert current_app.main == 'nexus_personal'
assert 'config.tasks' not in current_app.conf.include
from nexus_personal.worker_composition import TASK_MODULES, BEAT_SCHEDULE
assert tuple(current_app.conf.include) == TASK_MODULES
from pathlib import Path
assert Path(current_app.conf.beat_schedule_filename) == Path(settings.NEXUS_SHARED_STORAGE_ROOT).parent / 'run' / 'personal-beat'
assert current_app.conf.task_default_queue == 'personal-default'
assert current_app.conf.broker_transport_options['global_keyprefix'].startswith('nexus-personal:')
assert current_app.conf.worker_prefetch_multiplier == 1
current_app.loader.import_default_modules()
for name in ('simulate_agent_deployment', 'release_stale_agent_billing_reservations'):
    assert 'apps.agents.tasks.' + name not in current_app.tasks
    from apps.agents import tasks as agent_tasks
    assert not hasattr(agent_tasks, name)
for item in BEAT_SCHEDULE.values():
    assert item['task'] in current_app.tasks, item['task']
assert 'apps.providers.models' in sys.modules
print('production-host-cold-boot-ok')
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('production-host-cold-boot-ok', result.stdout)

    def test_entrypoints_refuse_enterprise_settings(self):
        for module in ('nexus_personal.asgi', 'nexus_personal.celery'):
            result = self.probe('import ' + module, {'DJANGO_SETTINGS_MODULE': 'config.settings'})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('require', result.stderr)

    def test_management_cannot_override_edition_or_launch_development_http(self):
        for args in (['check', '--settings=config.settings'], ['check', '--settings', 'config.settings'],
                     ['runserver', '0.0.0.0:8000']):
            result = self.probe('import sys; sys.argv = ' + repr(['personal-manage', *args]) +
                '; from nexus_personal.manage import main; main()')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('ImproperlyConfigured', result.stderr)

    def test_process_launcher_sets_role_before_actual_production_settings_load(self):
        for service in ('worker', 'beat', 'agent-worker'):
            result = self.probe('''
import os, sys
from unittest.mock import patch
from nexus_personal.processes import main
from celery import Celery
from django.conf import settings
service = ''' + repr(service) + '''
def dispatch(*args):
    assert settings.SETTINGS_MODULE == 'nexus_personal.settings'
    assert settings.NEXUS_PROCESS_ROLE == ('beat' if service == 'beat' else 'worker')
    assert os.environ['NEXUS_PROCESS_ROLE'] == settings.NEXUS_PROCESS_ROLE
    if service == 'agent-worker':
        assert sys.argv == ['personal-manage', 'run_agent_tasks', '--concurrency', '2', '--once']
    elif service == 'worker':
        assert '--queues=personal-default,dataset-imports' in args[1]
        if os.name == 'nt':
            assert '--pool=solo' in args[1]
    else:
        assert args[1] == ['beat', '--loglevel=WARNING']
    print('process-dispatch-checked')
with patch.object(Celery, 'worker_main', dispatch), patch.object(Celery, 'start', dispatch), \
     patch('django.core.management.execute_from_command_line', dispatch):
    main([service] + (['--concurrency', '2', '--once'] if service == 'agent-worker' else []))
''', {'NEXUS_PROCESS_ROLE': 'web'})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('process-dispatch-checked', result.stdout)

    def test_process_launcher_rejects_wrong_edition_invalid_arguments_and_loaded_settings(self):
        for args in (['worker', '--settings=config.settings'], ['worker', '--queues=cloud'],
                     ['agent-worker', '--concurrency=0'], ['beat', '--once'], ['beat', '--concurrency=2'],
                     ['web'], ['web', '--port=80'], ['web', '--port=8001', '--web-workers=17'],
                     ['web', '--port=8001', '--concurrency=2'], ['worker', '--port=8001'],
                     ['worker', '--local-http']):
            result = self.probe('from nexus_personal.processes import main; main(' + repr(args) + ')')
            self.assertNotEqual(result.returncode, 0)
        result = self.probe('from nexus_personal.processes import main; main(["worker"])',
                            {'DJANGO_SETTINGS_MODULE': 'config.settings'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('require nexus_personal.settings', result.stderr)
        result = self.probe('from django.conf import settings; settings.configure(); '
                            'from nexus_personal.processes import main; main(["worker"])')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('fresh interpreter', result.stderr)

    def test_web_launcher_requires_valid_bundle_and_uses_only_loopback_production_target(self):
        result = self.probe('from nexus_personal.processes import main; main(["web", "--port=8001"])')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('PERSONAL_FRONTEND_UNAVAILABLE', result.stderr)
        result = self.probe('''
from unittest.mock import patch
from django.conf import settings
from nexus_personal.processes import main
def dispatch(*args, **kwargs):
    assert settings.SETTINGS_MODULE == 'nexus_personal.settings'
    assert settings.NEXUS_PROCESS_ROLE == 'web'
    assert args == ('nexus_personal.asgi:application',)
    assert kwargs == dict(host='127.0.0.1', port=8001, workers=2, proxy_headers=False, access_log=False, log_level='warning')
    print('personal-web-dispatch-ok')
with patch('nexus_personal.frontend.load_bundle') as bundle, patch('uvicorn.run', dispatch):
    main(['web', '--port=8001', '--web-workers=2'])
    bundle.assert_called_once_with(settings.NEXUS_WEB_INDEX_PATH)
''', {'NEXUS_PROCESS_ROLE': 'worker'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'personal-web-dispatch-ok')

    def test_explicit_local_http_is_loopback_only_and_requires_the_configured_port(self):
        self.config['public_origin'] = 'https://127.0.0.1:8001/'
        self.path.write_text(json.dumps(self.config), encoding='utf-8')
        result = self.probe('''
from unittest.mock import patch
from django.conf import settings
from nexus_personal.processes import main
def dispatch(*args, **kwargs):
    assert settings.NEXUS_PUBLIC_BASE_URL == 'http://127.0.0.1:8001'
    assert settings.ALLOWED_HOSTS == ['127.0.0.1', 'localhost']
    assert settings.SESSION_COOKIE_SECURE is False and settings.CSRF_COOKIE_SECURE is False
    assert settings.SECURE_SSL_REDIRECT is False and settings.SECURE_HSTS_SECONDS == 0
    assert settings.SECURE_PROXY_SSL_HEADER is None
    assert kwargs['host'] == '127.0.0.1' and kwargs['port'] == 8001
    print('personal-local-http-dispatch-ok')
with patch('nexus_personal.frontend.load_bundle'), patch('uvicorn.run', dispatch):
    main(['web', '--port=8001', '--local-http'])
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'personal-local-http-dispatch-ok')

        self.config['public_origin'] = 'https://personal.example:8001/'
        self.path.write_text(json.dumps(self.config), encoding='utf-8')
        result = self.probe('from django.conf import settings; print(settings.NEXUS_PUBLIC_BASE_URL)',
                            {'NEXUS_PERSONAL_LOCAL_HTTP_PORT': '8001'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('PERSONAL_LOCAL_HTTP_INVALID', result.stderr)

        self.config['public_origin'] = 'https://127.0.0.1:8002/'
        self.path.write_text(json.dumps(self.config), encoding='utf-8')
        result = self.probe('from django.conf import settings; print(settings.NEXUS_PUBLIC_BASE_URL)',
                            {'NEXUS_PERSONAL_LOCAL_HTTP_PORT': '8001'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('PERSONAL_LOCAL_HTTP_INVALID', result.stderr)


def _postgres_test_admin_options(config):
    # The formal harness application role is intentionally NOCREATEDB. Use its
    # existing admin credential only for the random database's lifecycle, never
    # for the product host or migrations, and never ALTER ROLE to widen access.
    dedicated_admin = 'admin_password' in config
    password = config['admin_password'] if dedicated_admin else config['PASSWORD']
    if not password:
        raise ValueError('Explicit PostgreSQL test administrator credential is required.')
    return {'dbname': 'postgres', 'host': config['HOST'], 'port': config['PORT'],
            'user': 'postgres' if dedicated_admin else config['USER'], 'password': password,
            'autocommit': True, 'connect_timeout': 5}


class PersonalPostgresHarnessConfigTests(unittest.TestCase):
    def test_existing_harness_uses_admin_only_for_database_lifecycle(self):
        config = {'USER': 'restricted-app', 'PASSWORD': 'test-app-secret', 'admin_password': 'test-admin-secret',
                  'HOST': '127.0.0.1', 'PORT': 55432}
        before = dict(config)
        options = _postgres_test_admin_options(config)
        self.assertEqual((options['user'], options['password'], options['dbname']), ('postgres', 'test-admin-secret', 'postgres'))
        self.assertEqual(config, before)

    def test_explicit_createdb_test_role_remains_supported(self):
        config = {'USER': 'dedicated-test-role', 'PASSWORD': 'test-secret', 'HOST': '127.0.0.1', 'PORT': 5432}
        self.assertEqual(_postgres_test_admin_options(config)['user'], 'dedicated-test-role')

    def test_empty_explicit_admin_does_not_fall_back_to_application_password(self):
        with self.assertRaises(ValueError):
            _postgres_test_admin_options({'admin_password': '', 'PASSWORD': 'test-app-secret'})


@unittest.skipUnless(os.environ.get('NEXUS_PERSONAL_TEST_POSTGRES_CONFIG'), 'Explicit existing PostgreSQL test infrastructure required')
class PersonalHostPostgresTests(unittest.TestCase):
    setUp = PersonalHostTests.setUp
    probe = PersonalHostTests.probe

    def test_real_production_migrations_owner_session_and_csrf(self):
        import psycopg
        from psycopg import sql
        # No default lookup: only this opt-in acceptance test may read the local
        # harness credentials. Production settings never consult this file.
        connection = json.loads(Path(os.environ['NEXUS_PERSONAL_TEST_POSTGRES_CONFIG']).read_text(encoding='utf-8'))
        database_name = 'nexus_personal_' + uuid4().hex
        with psycopg.connect(**_postgres_test_admin_options(connection)) as admin:
            admin.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(
                sql.Identifier(database_name), sql.Identifier(connection['USER'])))
            try:
                self.config['database'].update(name=database_name, host=connection['HOST'],
                    port=int(connection['PORT']), user=connection['USER'], password=connection['PASSWORD'])
                self.path.write_text(json.dumps(self.config), encoding='utf-8')
                result = self.probe('''
import importlib.abc, sys, io, secrets
class BlockPrivate(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p + '.') for p in (
            'config', 'nexus_enterprise', 'nexus_personal.tests', 'apps.iam',
            'apps.billing', 'apps.tokenbank', 'apps.marketplace', 'apps.api_keys')):
            raise ModuleNotFoundError('Forbidden production dependency: ' + fullname)
sys.meta_path.insert(0, BlockPrivate())
from nexus_personal.asgi import application
from django.core.management import call_command, CommandError
from django.db import connection
from django.conf import settings
from rest_framework.test import APIClient
call_command('migrate', interactive=False, verbosity=0)
client = APIClient(enforce_csrf_checks=True)
headers = {'HTTP_HOST': 'personal.example:9443'}
assert client.get('/api/v1/public/bootstrap/', secure=True, **headers).status_code == 503
password = secrets.token_urlsafe(32)
sys.stdin = io.StringIO(password + '\\n')
call_command('setup_personal', email='owner@example.test', password_stdin=True, stdout=io.StringIO())
sys.stdin = io.StringIO(password + '\\n')
try:
    call_command('setup_personal', email='other@example.test', password_stdin=True, stdout=io.StringIO())
except CommandError:
    pass
else:
    raise AssertionError('Setup replaced an existing owner')
bootstrap = client.get('/api/v1/public/bootstrap/', secure=True, **headers)
assert bootstrap.status_code == 200
assert bootstrap.data['distribution'] == 'community'
origin = settings.NEXUS_PUBLIC_BASE_URL
login = {'email': 'owner@example.test', 'password': password}
assert client.post('/api/v1/auth/login/', login, format='json', secure=True,
    HTTP_ORIGIN=origin, **headers).status_code == 403
csrf = client.cookies['csrftoken'].value
response = client.post('/api/v1/auth/login/', login, format='json', secure=True,
    HTTP_ORIGIN=origin, HTTP_X_CSRFTOKEN=csrf, HTTP_X_NEXUS_CLIENT='web', **headers)
assert response.status_code == 200, response.status_code
assert 'access_token' not in response.data
assert response.cookies[settings.SESSION_COOKIE_NAME]['secure']
assert response.cookies[settings.SESSION_COOKIE_NAME]['httponly']
assert client.get('/api/v1/auth/whoami/', secure=True, **headers).status_code == 200
assert client.get('/api/v1/auth/whoami/', secure=True, HTTP_X_NEXUS_TENANT='foreign', **headers).status_code == 403
assert client.get('/api/v1/auth/whoami/', secure=True, HTTP_HOST='evil.example').status_code == 400
assert client.get('/api/v1/auth/whoami/', **headers).status_code == 301
assert client.post('/api/v1/auth/logout/', secure=True, **headers).status_code == 403
assert client.post('/api/v1/auth/logout/', secure=True, HTTP_ORIGIN='https://evil.example',
    HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value, **headers).status_code == 403
assert client.post('/api/v1/auth/logout/', secure=True, HTTP_ORIGIN=origin,
    HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value, **headers).status_code == 200
assert client.get('/api/v1/auth/whoami/', secure=True, **headers).status_code == 401
tables = connection.introspection.table_names()
assert not any(name.startswith(('iam_', 'billing_', 'tokenbank_', 'api_keys_', 'marketplace_')) for name in tables)
print('production-postgres-owner-csrf-ok')
connection.close()
''')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('production-postgres-owner-csrf-ok', result.stdout)
            finally:
                # This exact random database was created above, never an existing
                # Cloud database. Close only its connections and remove it.
                self.assertRegex(database_name, r'^nexus_personal_[0-9a-f]{32}$')
                admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database_name)))
