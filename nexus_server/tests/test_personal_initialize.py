"""Real initialization against the existing opt-in PostgreSQL infrastructure.

Only exact, randomly named databases created by this fixture are removed.
No service listener, administrative role alteration or production schema change.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import unittest
from uuid import uuid4

from tests import test_personal_host as host
from tests import test_personal_install as preparation


TIMING_PREFIX = 'INITIALIZATION_TIMING '
TIMING_PHASES = frozenset({'bootstrap_start', 'django_setup_start', 'django_setup_end',
                         'migrate_start', 'migrate_end', 'owner_start', 'owner_end', 'migration_progress'})


def _test_redis_url(port):
    """Optional owned loopback Redis fixture; never accept URLs or credentials."""
    if port is None:
        return None
    if not isinstance(port, str) or not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise ValueError('Invalid test Redis port')
    return f'redis://127.0.0.1:{int(port)}/12'


@contextmanager
def initialization_trace(root, *, enabled=False):
    """Opt-in test-only phase timing; never filter or replace CLI output/errors."""
    if not enabled:
        yield ''
        return
    path = root / ('initialize-timing-' + uuid4().hex + '.jsonl')
    with path.open('x', encoding='utf-8'):
        pass
    path.chmod(0o600)
    prefix = '''
import json, time
_timing_started = time.monotonic()
def _timing_emit(phase, **extra):
    with open(_timing_path, 'a', encoding='utf-8') as stream:
        stream.write(json.dumps({'phase':phase,'elapsed':round(time.monotonic()-_timing_started,3),**extra})+'\\n')
def _timing_wrap(phase, original, emit):
    def wrapped(*args, **kwargs):
        emit(phase+'_start')
        try:
            return original(*args, **kwargs)
        finally:
            emit(phase+'_end')
    return wrapped
_timing_emit('bootstrap_start')
import django
_timing_original_setup = django.setup
def _timing_setup(*args, **kwargs):
    value = _timing_wrap('django_setup', _timing_original_setup, _timing_emit)(*args, **kwargs)
    from django.core import management
    management.call_command = _timing_wrap('migrate', management.call_command, _timing_emit)
    from nexus_personal import services
    services.provision_owner = _timing_wrap('owner', services.provision_owner, _timing_emit)
    from django.db.migrations.executor import MigrationExecutor
    original = MigrationExecutor.__init__
    def executor(self, connection, progress_callback=None):
        count = 0
        def callback(action, *args, **kwargs):
            nonlocal count
            if action == 'apply_success':
                count += 1
                if count % 20 == 0:
                    _timing_emit('migration_progress', completed=count)
            if progress_callback is not None:
                return progress_callback(action, *args, **kwargs)
        return original(self, connection, progress_callback=callback)
    MigrationExecutor.__init__ = executor
    return value
django.setup = _timing_setup
'''
    try:
        yield '_timing_path = ' + repr(str(path)) + '\n' + prefix
    finally:
        try:
            import math
            if path.stat().st_size > 65536:
                raise ValueError()
            records = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
            for record in records:
                if (not isinstance(record, dict) or set(record) not in
                        ({'phase', 'elapsed'}, {'phase', 'elapsed', 'completed'})
                        or record['phase'] not in TIMING_PHASES
                        or type(record['elapsed']) not in (int, float)
                        or not math.isfinite(record['elapsed']) or not 0 <= record['elapsed'] <= 12000
                        or ('completed' in record and
                            (record['phase'] != 'migration_progress' or type(record['completed']) is not int
                             or not 0 <= record['completed'] <= 10000))):
                    raise ValueError()
            for record in records:
                print('\n' + TIMING_PREFIX + json.dumps(record, sort_keys=True), file=sys.stderr, flush=True)
        except (OSError, ValueError, TypeError, KeyError):
            raise AssertionError('Invalid initialization timing trace') from None
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                raise AssertionError('Initialization timing cleanup failed') from None


@unittest.skipUnless(os.environ.get('NEXUS_PERSONAL_TEST_POSTGRES_CONFIG'),
                     'Explicit existing PostgreSQL test infrastructure required')
class PersonalInitializationPostgresTests(unittest.TestCase):
    setUp = preparation.PersonalInstallationPreparationTests.setUp
    write_infra = preparation.PersonalInstallationPreparationTests.write_infra
    write_manifest = preparation.PersonalInstallationPreparationTests.write_manifest
    prepare = preparation.PersonalInstallationPreparationTests.prepare

    @contextmanager
    def database(self):
        import psycopg
        from psycopg import sql
        redis_url = _test_redis_url(os.environ.get('NEXUS_PERSONAL_TEST_REDIS_PORT'))
        if redis_url is not None:
            self.infra['redis_url'] = redis_url
        config = json.loads(Path(os.environ['NEXUS_PERSONAL_TEST_POSTGRES_CONFIG']).read_text(encoding='utf-8'))
        name = 'nexus_personal_' + uuid4().hex
        with psycopg.connect(**host._postgres_test_admin_options(config)) as admin:
            admin.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(name), sql.Identifier(config['USER'])))
            try:
                self.infra['database'].update(name=name, host=config['HOST'], port=int(config['PORT']),
                                             user=config['USER'], password=config['PASSWORD'])
                self.write_infra()
                bundle = os.environ.get('NEXUS_PERSONAL_TEST_WEB_BUNDLE')
                self.prepare(**({'web_bundle': Path(bundle)} if bundle else {}))
                with psycopg.connect(dbname=name, host=config['HOST'], port=config['PORT'],
                        user=config['USER'], password=config['PASSWORD'], autocommit=True) as app:
                    yield app
            finally:
                self.assertRegex(name, r'^nexus_personal_[0-9a-f]{32}$')
                admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))

    def initialize(self, *, email='owner@example.test', password=None, inject='', directory=None):
        password = password if password is not None else secrets.token_urlsafe(32)
        code = '''
import importlib.abc,sys,runpy
class BlockPrivate(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if any(fullname == p or fullname.startswith(p+'.') for p in (
            'config','nexus_enterprise','nexus_personal.tests','apps.iam','apps.billing',
            'apps.tokenbank','apps.marketplace','apps.api_keys')):
            raise AssertionError('Private production dependency')
sys.meta_path.insert(0,BlockPrivate())
'''
        with initialization_trace(self.root, enabled=os.environ.get('NEXUS_PERSONAL_TEST_INITIALIZATION_TIMING') == '1') as timing:
            code += timing + inject + "\nrunpy.run_module('nexus_personal.install',run_name='__main__')\n"
            result = subprocess.run([sys.executable, '-X', 'utf8', '-c', code, 'initialize', '--directory',
                str(directory or self.destination), '--email', email, '--password-stdin'],
                cwd=Path(__file__).resolve().parents[1], input=password + '\n', capture_output=True, text=True, encoding='utf-8',
                env={**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.settings',
                     'DATABASE_URL': 'postgresql://must-not-use-enterprise'}, timeout=45)
        for secret in (password, self.infra['database']['password'], 'fault-sensitive-sentinel'):
            self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        return result

    def error(self, result, code):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stderr)['error'], code)
        self.assertEqual(result.stdout, '')

    def test_installed_durable_worker_loss_and_offline_controller_are_recoverable(self):
        from tests.test_personal_controllers import DENY_PRIVATE
        self.assertTrue(os.environ.get('NEXUS_PERSONAL_TEST_REDIS_PORT'),
                        'Worker acceptance requires an explicitly owned Redis fixture')
        with self.database():
            password = secrets.token_urlsafe(32)
            initialized = self.initialize(password=password)
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            installed_root = os.environ.get('NEXUS_TEST_INSTALLED_SERVER_ROOT')
            if installed_root:
                import nexus_personal
                server = Path(installed_root)
                self.assertTrue(server.is_absolute())
                server = server.resolve(strict=True)
                self.assertEqual(server, Path(nexus_personal.__file__).resolve().parents[1],
                                 'Installed worker cannot fall back to checkout code')
                fixture_root = Path(__file__).resolve().parents[1]
                self.assertFalse(fixture_root.is_relative_to(server),
                                 'Acceptance fixtures must not be installed as product modules')
                paths = [str(server), str(fixture_root)]
            else:
                from tools.community_release.linux_acceptance import stage
                source = self.root / 'filtered-worker-source'
                stage(Path(__file__).resolve().parents[2], source)
                server = source / 'nexus_server'
                paths = [str(server), str(source / 'nexus_openwrt/sdk/nexus-agent-sdk-python/src')]
            environment = {**os.environ, 'NEXUS_PERSONAL_CONFIG': str(self.destination / 'host.json'),
                'DJANGO_SETTINGS_MODULE': 'nexus_personal.settings',
                'PYTHONPATH': os.pathsep.join(paths)}
            code = DENY_PRIVATE + '\nfrom tests.personal_durable_worker_probe import run; run(sys.stdin.readline().strip(), ' + repr(self.infra['database']['name']) + ')'
            result = subprocess.run([sys.executable, '-X', 'utf8', '-c', code], input=password + '\n',
                cwd=server, env=environment, capture_output=True, text=True, encoding='utf-8', timeout=120)
            for secret in (password, self.infra['database']['password']):
                self.assertNotIn(secret, result.stdout + result.stderr)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertFalse(report.pop('hosted_deployment_verified'))
            self.assertEqual(len(report), 6)
            self.assertTrue(all(report.values()), report)

    def test_real_initialize_repeat_owner_isolation_and_installed_login(self):
        with self.database() as app:
            password = secrets.token_urlsafe(32)
            result = self.initialize(password=password)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report['database_state'], 'initialized')
            self.assertTrue(report['owner_created_by_command'])
            self.assertFalse(report['services_started_by_command'])
            self.assertEqual(report['service_health'], 'not_checked')
            self.assertNotIn('database_migration_and_owner_setup', report['unverified_steps'])
            before = app.execute('SELECT password FROM auth_user').fetchone()
            repeated = self.initialize()
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertFalse(json.loads(repeated.stdout)['owner_created_by_command'])
            self.assertEqual(before, app.execute('SELECT password FROM auth_user').fetchone())
            self.error(self.initialize(email='other@example.test'), 'INSTALL_OWNER_MISMATCH')
            self.assertEqual(app.execute('SELECT count(*) FROM auth_user').fetchone()[0], 1)
            second = self.root / 'other-installation'
            self.prepare(directory=second)
            self.error(self.initialize(directory=second), 'INSTALL_DATABASE_IDENTITY_MISMATCH')
            code = '''
import os,sys
os.environ['DJANGO_SETTINGS_MODULE']='nexus_personal.settings'
from nexus_personal.asgi import application
from rest_framework.test import APIClient
c=APIClient(enforce_csrf_checks=True)
headers={'HTTP_HOST':'personal.example:9443'}
assert c.get('/api/v1/public/bootstrap/',secure=True,**headers).status_code == 200
assert c.get('/api/v1/topology/',secure=True,**headers).status_code in (401,403)
data={'email':'owner@example.test','password':sys.stdin.readline().strip()}
denied=c.post('/api/v1/auth/login/',data,format='json',secure=True,HTTP_ORIGIN='https://personal.example:9443',**headers)
assert denied.status_code == 403, denied.status_code
r=c.post('/api/v1/auth/login/',data,format='json',secure=True,HTTP_ORIGIN='https://personal.example:9443',HTTP_X_CSRFTOKEN=c.cookies['csrftoken'].value,HTTP_X_NEXUS_CLIENT='web',**headers)
assert r.status_code == 200 and r.cookies['nexus_personal_session']['secure']
assert c.get('/api/v1/auth/whoami/',secure=True,**headers).status_code == 200
graph=c.get('/api/v1/topology/',secure=True,**headers)
assert graph.status_code == 200, graph.status_code
assert all(graph.data[name] == [] for name in ('runtimes','sources','pools','routers'))
assert graph.data['summary']['runtime_count'] == 0
assert c.get('/api/v1/auth/whoami/',secure=True,HTTP_X_NEXUS_TENANT='foreign',**headers).status_code == 403
print('installed-owner-login-ok')
'''
            login = subprocess.run([sys.executable,'-X','utf8','-c',code], input=password+'\n',capture_output=True,text=True, encoding='utf-8',
                env={**os.environ,'NEXUS_PERSONAL_CONFIG':str(self.destination/'host.json')},timeout=45)
            self.assertEqual(login.returncode,0,login.stderr)
            self.assertEqual(login.stdout.strip(),'installed-owner-login-ok')

    def test_provider_probe_transactions_and_stale_results_on_initialized_database(self):
        with self.database():
            initialized = self.initialize()
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            code = '''
import importlib.abc,sys
class BlockPrivate(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if any(fullname == p or fullname.startswith(p+'.') for p in (
            'config','nexus_enterprise','nexus_personal.tests','apps.iam','apps.billing',
            'apps.tokenbank','apps.marketplace','apps.api_keys')):
            raise AssertionError('Private production dependency')
sys.meta_path.insert(0,BlockPrivate())
import django
django.setup()
from tests.personal_provider_probe import run
from tests.personal_dataset_import_probe import run as run_import_guards
from tests.personal_dataset_transfer_probe import run as run_transfer_guards
from tests.personal_notification_probe import run as run_notification_guards
from tests.personal_python_build_probe import run as run_python_build_guards
import json
print(json.dumps(run_python_build_guards(sys.argv[1])))
run(sys.argv[1])
import json
print(json.dumps(run_notification_guards(sys.argv[1])))
print(json.dumps(run_transfer_guards(sys.argv[1])))
print(json.dumps(run_import_guards(sys.argv[1])))
import os
if os.environ.get('NEXUS_PERSONAL_TEST_S3_ENDPOINT'):
    from tests.personal_dataset_s3_probe import run as run_s3_guards
    print(json.dumps(run_s3_guards(os.environ['NEXUS_PERSONAL_TEST_S3_ENDPOINT'])))
'''
            result = subprocess.run([sys.executable, '-X', 'utf8', '-c', code, self.infra['database']['name']],
                env={**os.environ, 'DJANGO_SETTINGS_MODULE': 'nexus_personal.settings',
                     'NEXUS_PERSONAL_CONFIG': str(self.destination / 'host.json')},
                capture_output=True, text=True, encoding='utf-8', timeout=45)
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = [
                {'scope': 'personal-python-build-postgres-guards',
                 'tests_run': 21, 'skipped': 0, 'vendor': 'postgresql'},
                {'scope': 'personal-provider-postgres-guards',
                 'tests_run': 8, 'skipped': 0, 'vendor': 'postgresql'},
                {'scope': 'personal-notification-postgres-guards',
                 'tests_run': 3, 'skipped': 0, 'vendor': 'postgresql'},
                {'scope': 'personal-dataset-transfer-postgres-guards',
                 'tests_run': 4, 'skipped': 0, 'vendor': 'postgresql'},
                {'scope': 'personal-dataset-import-postgres-guards',
                 'tests_run': 2, 'skipped': 0, 'vendor': 'postgresql'}]
            if os.environ.get('NEXUS_PERSONAL_TEST_S3_ENDPOINT'):
                expected.append({'scope': 'personal-dataset-s3-guards', 'tests_run': 1,
                                 'skipped': 0, 'payload_bytes': 128 * 1024 * 1024})
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()], expected)

    def test_nonempty_database_and_invalid_password_do_not_create_schema(self):
        with self.database() as app:
            app.execute('CREATE TABLE keep_me (value integer)')
            self.error(self.initialize(), 'INSTALL_DATABASE_NOT_EMPTY')
            self.assertEqual(app.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall(), [('keep_me',)])
            app.execute('DROP TABLE keep_me')  # Exact fixture table, not user data.
            self.error(self.initialize(password='short'), 'INSTALL_IDENTITY_INVALID')
            self.assertEqual(app.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall(), [])

    def test_database_lock_and_interrupted_migration_are_not_replayed(self):
        from nexus_personal.initialize import LOCK_ID
        with self.database() as app:
            app.execute('SELECT pg_advisory_lock(%s)', [LOCK_ID])
            try:
                self.error(self.initialize(), 'INSTALL_INITIALIZATION_BUSY')
            finally:
                app.execute('SELECT pg_advisory_unlock(%s)', [LOCK_ID])
            injected = '''
from django.core import management
def fail_migration(*args,**kwargs):
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute('CREATE TABLE partial_migration (value integer)')
    raise RuntimeError('fault-sensitive-sentinel')
management.call_command=fail_migration
'''
            self.error(self.initialize(inject=injected), 'INSTALL_INITIALIZATION_FAILED')
            self.assertEqual(app.execute('SELECT state FROM nexus_personal_bootstrap').fetchone()[0], 'migrating')
            self.error(self.initialize(), 'INSTALL_INITIALIZATION_INCOMPLETE')
            self.assertEqual(set(app.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()),
                             {('nexus_personal_bootstrap',), ('partial_migration',)})

    def test_owner_failure_preserves_schema_and_retry_does_not_rerun_migration(self):
        with self.database() as app:
            injected = '''
import django
original_setup=django.setup
def setup(*args,**kwargs):
    original_setup(*args,**kwargs)
    from nexus_personal import services
    def fail_owner(**kwargs): raise RuntimeError('fault-sensitive-sentinel')
    services.provision_owner=fail_owner
django.setup=setup
'''
            self.error(self.initialize(inject=injected), 'INSTALL_INITIALIZATION_FAILED')
            self.assertEqual(app.execute('SELECT state FROM nexus_personal_bootstrap').fetchone()[0], 'schema_ready')
            self.assertEqual(app.execute('SELECT count(*) FROM auth_user').fetchone()[0], 0)
            before = app.execute('SELECT count(*) FROM django_migrations').fetchone()
            retry = self.initialize(inject="from django.core import management\ndef forbidden(*a,**kw): raise AssertionError('Migration replay')\nmanagement.call_command=forbidden\n")
            self.assertEqual(retry.returncode, 0, retry.stderr)
            self.assertEqual(before, app.execute('SELECT count(*) FROM django_migrations').fetchone())
            self.assertEqual(app.execute('SELECT state FROM nexus_personal_bootstrap').fetchone()[0], 'initialized')


class InitializationTimingTests(unittest.TestCase):
    def test_disabled_timing_does_not_create_files_or_change_code(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with initialization_trace(root) as prefix:
                self.assertEqual(prefix, '')
                self.assertEqual(list(root.iterdir()), [])
            self.assertEqual(list(root.iterdir()), [])

    def test_timing_reports_only_checked_values_and_preserves_original_exception(self):
        import io
        import tempfile
        from contextlib import redirect_stderr
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = io.StringIO()
            failure = RuntimeError('test-only-operation-failure')
            with redirect_stderr(output), self.assertRaises(RuntimeError) as caught:
                with initialization_trace(root, enabled=True) as prefix:
                    compile(prefix, '<timing>', 'exec')
                    path, = root.iterdir()
                    path.write_text(json.dumps({'phase':'django_setup_end','elapsed':0.25})+'\n', encoding='utf-8')
                    raise failure
            self.assertIs(caught.exception, failure)
            self.assertEqual(output.getvalue().strip(), TIMING_PREFIX+'{"elapsed": 0.25, "phase": "django_setup_end"}')
            self.assertNotIn(directory, output.getvalue())
            self.assertEqual(list(root.iterdir()), [])

    def test_timing_rejects_unsafe_payloads_without_logging_them(self):
        import io
        import tempfile
        from contextlib import redirect_stderr
        for invalid in ('not-json-sensitive-sentinel', json.dumps({'phase':'sensitive-sentinel','elapsed':1}),
                        json.dumps({'phase':'django_setup_end','elapsed':True}),
                        json.dumps({'phase':'django_setup_end','elapsed':float('nan')}),
                        json.dumps({'phase':'django_setup_end','elapsed':1,'secret':'sensitive-sentinel'}),
                        json.dumps({'phase':'migration_progress','elapsed':1,'completed':'sensitive-sentinel'}),
                        'x'*65537):
            with self.subTest(size=len(invalid)), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                output = io.StringIO()
                with redirect_stderr(output), self.assertRaisesRegex(AssertionError, '^Invalid initialization timing trace$'):
                    with initialization_trace(root, enabled=True):
                        path, = root.iterdir()
                        path.write_text(invalid, encoding='utf-8')
                self.assertEqual(output.getvalue(), '')
                self.assertEqual(list(root.iterdir()), [])

    def test_timing_does_not_preimport_cli_or_change_real_cli_error(self):
        import io
        import tempfile
        from contextlib import redirect_stderr
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = "import runpy; runpy.run_module('nexus_personal.install',run_name='__main__')"
            arguments = ['initialize', '--directory', str(root / 'missing'),
                         '--email', 'owner@example.test', '--password-stdin']
            plain = subprocess.run([sys.executable, '-X', 'utf8', '-c', entry, *arguments],
                                   capture_output=True, text=True, encoding='utf-8', input='', timeout=15)
            with redirect_stderr(io.StringIO()), initialization_trace(root, enabled=True) as prefix:
                traced = subprocess.run([sys.executable, '-X', 'utf8', '-c', prefix + '\n' + entry, *arguments],
                                        capture_output=True, text=True, encoding='utf-8', input='', timeout=15)
            self.assertEqual(plain.returncode, 1)
            self.assertEqual(json.loads(plain.stderr)['error'], 'INSTALL_INPUT_MISSING')
            self.assertEqual((traced.returncode, traced.stdout, traced.stderr),
                             (plain.returncode, plain.stdout, plain.stderr))
            self.assertEqual(list(root.iterdir()), [])

    def test_actual_generated_wrapper_preserves_values_arguments_and_exceptions(self):
        import ast
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            with initialization_trace(Path(directory), enabled=True) as prefix:
                definition = next(node for node in ast.parse(prefix).body
                                  if isinstance(node, ast.FunctionDef) and node.name == '_timing_wrap')
                namespace = {}
                exec(compile(ast.Module(body=[definition], type_ignores=[]), '<timing-wrapper>', 'exec'), namespace)
                events = []
                marker = object()
                def original(value, *, secret):
                    self.assertIs(value, marker)
                    self.assertEqual(secret, 'sensitive-sentinel')
                    return marker
                wrapped = namespace['_timing_wrap']('owner', original, events.append)
                self.assertIs(wrapped(marker, secret='sensitive-sentinel'), marker)
                self.assertEqual(events, ['owner_start', 'owner_end'])
                error = ValueError('sensitive-sentinel')
                def fail():
                    raise error
                with self.assertRaises(ValueError) as caught:
                    namespace['_timing_wrap']('owner', fail, events.append)()
                self.assertIs(caught.exception, error)
                self.assertEqual(events, ['owner_start', 'owner_end']*2)


if __name__ == '__main__':
    unittest.main()
