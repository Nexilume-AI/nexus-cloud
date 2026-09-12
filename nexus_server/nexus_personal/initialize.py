"""Explicit first initialization of a prepared, independently owned database.

The bootstrap stamp is operational metadata, deliberately available before the
Django schema exists. Non-atomic migrations cannot be safely replayed merely
because a client lost its response; an interrupted migration fails closed.
"""
import getpass
import hashlib
import io
import os
import sys

from .install import InstallationError, _fail, _path, check


LOCK_ID = 66865407134237491  # PostgreSQL advisory locks are database-local.


def initialize(*, directory, email, display_name='', password_stdin=False):
    result = check(directory=directory)
    root = _path(directory)
    from django.conf import settings
    if settings.configured:
        _fail('INSTALL_FRESH_PROCESS_REQUIRED')
    os.environ['NEXUS_PERSONAL_CONFIG'] = str(root / 'host.json')
    os.environ['DJANGO_SETTINGS_MODULE'] = 'nexus_personal.settings'
    password = ''
    connection = None
    locked = False
    try:
        import django
        django.setup()
        from django.contrib.auth import get_user_model
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError
        from django.core.management import call_command
        from django.core.validators import validate_email
        from django.db import connection, transaction
        from django.db.migrations.executor import MigrationExecutor
        from .models import PersonalInstallation
        from .services import installation_context, provision_owner

        email, display_name = email.strip().lower(), display_name.strip()
        try:
            validate_email(email)
            if len(email) > 254 or len(display_name) > 255:
                raise ValidationError('Invalid identity')
        except ValidationError:
            _fail('INSTALL_IDENTITY_INVALID')
        fingerprint = hashlib.sha256((root / 'host.json').read_bytes()).hexdigest()
        if connection.vendor != 'postgresql':
            _fail('INSTALL_POSTGRESQL_REQUIRED')
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(%s)', [LOCK_ID])
            locked = cursor.fetchone()[0]
            if not locked:
                _fail('INSTALL_INITIALIZATION_BUSY')
            cursor.execute('SELECT current_schema()')
            if cursor.fetchone()[0] != 'public':
                _fail('INSTALL_DATABASE_SCHEMA_UNSUPPORTED')
            cursor.execute("""SELECT n.nspname, c.relname FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
                  AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')""")
            relations = set(cursor.fetchall())
            if ('public', 'nexus_personal_bootstrap') in relations:
                cursor.execute('SELECT instance_id, config_hash, state FROM public.nexus_personal_bootstrap WHERE slot = 1')
                stamp = cursor.fetchone()
                if not stamp or stamp[:2] != (result['instance_id'], fingerprint):
                    _fail('INSTALL_DATABASE_IDENTITY_MISMATCH')
                state = stamp[2]
                if state not in ('schema_ready', 'initialized'):
                    _fail('INSTALL_INITIALIZATION_INCOMPLETE')
            elif relations:
                _fail('INSTALL_DATABASE_NOT_EMPTY')
            else:
                state = 'new'

            if state == 'initialized':
                executor = MigrationExecutor(connection)
                executor.loader.check_consistent_history(connection)
                if executor.migration_plan(executor.loader.graph.leaf_nodes()):
                    _fail('INSTALL_SCHEMA_UPGRADE_REQUIRED')
                installation_context()
                owner = PersonalInstallation.objects.select_related('owner').get(slot=1).owner
                if owner.email.strip().lower() != email:
                    _fail('INSTALL_OWNER_MISMATCH')
            else:
                if password_stdin:
                    password = sys.stdin.readline(4098)
                    if len(password) > 4096:
                        _fail('INSTALL_IDENTITY_INVALID')
                    password = password.removesuffix('\n').removesuffix('\r')
                else:
                    if not sys.stdin.isatty():
                        _fail('INSTALL_PASSWORD_INPUT_REQUIRED')
                    password = getpass.getpass('Owner password: ')
                    if password != getpass.getpass('Confirm password: '):
                        _fail('INSTALL_PASSWORD_CONFIRMATION_FAILED')
                try:
                    if len(password) > 4096:
                        raise ValidationError('Invalid identity')
                    validate_password(password, user=get_user_model()(username='nexus-owner', email=email))
                except ValidationError:
                    _fail('INSTALL_IDENTITY_INVALID')
                if state == 'new':
                    with transaction.atomic():
                        cursor.execute("""CREATE TABLE public.nexus_personal_bootstrap (
                            slot smallint PRIMARY KEY CHECK (slot = 1),
                            instance_id varchar(36) NOT NULL, config_hash varchar(64) NOT NULL,
                            state varchar(16) NOT NULL CHECK (state IN ('migrating', 'schema_ready', 'initialized')))""")
                        cursor.execute("INSERT INTO public.nexus_personal_bootstrap VALUES (1, %s, %s, 'migrating')",
                                       [result['instance_id'], fingerprint])
                    # Deliberately not one outer transaction: the existing graph
                    # includes CREATE INDEX CONCURRENTLY. Keep its semantics.
                    call_command('migrate', interactive=False, verbosity=0, stdout=io.StringIO(), stderr=io.StringIO())
                    cursor.execute("UPDATE public.nexus_personal_bootstrap SET state = 'schema_ready' WHERE slot = 1")
                executor = MigrationExecutor(connection)
                executor.loader.check_consistent_history(connection)
                if executor.migration_plan(executor.loader.graph.leaf_nodes()):
                    _fail('INSTALL_SCHEMA_UPGRADE_REQUIRED')
                with transaction.atomic():
                    provision_owner(email=email, password=password, display_name=display_name)
                    cursor.execute("UPDATE public.nexus_personal_bootstrap SET state = 'initialized' WHERE slot = 1")
                installation_context()

        return {**result, 'state': 'initialized', 'database_state': 'initialized',
                'owner_created_by_command': state != 'initialized',
                'unverified_steps': [step for step in result['unverified_steps']
                                     if step != 'database_migration_and_owner_setup']}
    except InstallationError:
        raise
    except Exception:
        # Backend, validation and migration exceptions may contain credentials
        # or user input. CLI callers receive a stable code, never the raw error.
        _fail('INSTALL_INITIALIZATION_FAILED')
    finally:
        password = ''
        if connection is not None:
            try:
                if locked:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_advisory_unlock(%s)', [LOCK_ID])
            except Exception:
                pass
            finally:
                connection.close()
