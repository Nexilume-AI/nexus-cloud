"""Prepare or initialize a private Community installation without starting services.

This is an installation step, not a complete installer or release approval.
Infrastructure credentials come only from an explicitly supplied protected file.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import socket
from uuid import uuid4

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured

from .frontend import load_bundle, MANIFEST
from .host_config import load_config, read_protected_json, validate_config
from .installation_errors import InstallationError


def _fail(code):
    raise InstallationError(code) from None


def _path(value, *, exists=True):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        _fail('INSTALL_PATH_INVALID')
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            _fail('INSTALL_LINK_REJECTED')
    if exists and not path.exists():
        _fail('INSTALL_INPUT_MISSING')
    return path


def _private_directory(path, *, create=False):
    _path(path, exists=not create)
    if create:
        # Exclusive reservation: do not chmod/adopt/replace an existing folder.
        path.mkdir(mode=0o700)
    if not path.is_dir():
        _fail('INSTALL_DIRECTORY_INVALID')
    if os.name == 'nt':
        # No secret exists before this new directory's inherited ACL is removed.
        # Existing directories are verified, never silently re-permissioned.
        script = r'''
$ErrorActionPreference = 'Stop'
$path = $env:NEXUS_INSTALL_ACL_TARGET
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$acl = [System.IO.Directory]::GetAccessControl($path)
if ($env:NEXUS_INSTALL_ACL_CREATE -eq '1') {
  $acl.SetOwner($sid)
  $acl.SetAccessRuleProtection($true, $false)
  foreach ($rule in @($acl.Access)) { [void]$acl.RemoveAccessRuleSpecific($rule) }
  $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
  [System.IO.Directory]::SetAccessControl($path, $acl)
  $acl = [System.IO.Directory]::GetAccessControl($path)
}
$allowed = @($sid.Value, 'S-1-5-18', 'S-1-5-32-544')
if ($acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -ne $sid.Value) { exit 1 }
$rules = $acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])
if ($rules.Count -eq 0) { exit 1 }
foreach ($rule in $rules) {
  if ($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin $allowed) { exit 1 }
}
'''
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
            env={**os.environ, 'NEXUS_INSTALL_ACL_TARGET': str(path), 'NEXUS_INSTALL_ACL_CREATE': '1' if create else '0'},
            capture_output=True, timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            _fail('INSTALL_PERMISSIONS_FAILED')
    else:
        info = path.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            _fail('INSTALL_PERMISSIONS_FAILED')


def _write(path, body):
    _path(path, exists=False)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(path, flags, 0o600), 'wb') as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())


def _json(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':')) + '\n').encode('utf-8')


def _bundle(path):
    # Bypass process cache: each prepare/check must inspect the current release.
    try:
        return load_bundle.__wrapped__(str(_path(path) / 'index.html'))
    except (ValueError, TypeError, KeyError, OSError, RecursionError):
        _fail('INSTALL_FRONTEND_INVALID')


def _infrastructure(path):
    source = _path(path)
    info = source.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 32768:
        _fail('INSTALL_INFRASTRUCTURE_INVALID')
    value = read_protected_json(str(source))
    if (not isinstance(value, dict) or set(value) - {'controllers', 'monitoring', 'python_builder'} != {'schema_version', 'database', 'redis_url'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1):
        _fail('INSTALL_INFRASTRUCTURE_INVALID')
    controllers = value.get('controllers', {})
    if not isinstance(controllers, dict) or set(controllers) - {'agent', 'provider'}:
        _fail('INSTALL_INFRASTRUCTURE_INVALID')
    prepared = {}
    for kind, fields in controllers.items():
        expected = {'port', 'image_admission_command', 'egress_policy_ready'} if kind == 'agent' else {'port', 'release_dir'}
        optional = {'network_policy'} if kind == 'agent' else set()
        if not isinstance(fields, dict) or not expected <= set(fields) or set(fields) - expected - optional:
            _fail('INSTALL_INFRASTRUCTURE_INVALID')
        prepared[kind] = {**fields, 'token': secrets.token_urlsafe(48)}
    return {**value, 'controllers': prepared}


def prepare(*, directory, origin, infrastructure_file, web_bundle):
    """Reserve a new directory, prepare everything, publish host config last.

    Failures leave a private incomplete directory for explicit inspection. No
    recursive rollback, overwrite, database access or process launch occurs.
    """
    try:
        root = _path(directory, exists=False)
        source = Path(__file__).resolve().parents[1]
        if root.exists():
            _fail('INSTALL_DESTINATION_EXISTS')
        if (root == Path.home().resolve() or root == Path(root.anchor)
                or root.is_relative_to(source) or source.is_relative_to(root)):
            _fail('INSTALL_PATH_INVALID')
        _path(root.parent)
        infra = _infrastructure(infrastructure_file)
        bundle = _bundle(web_bundle)
        # Everything after this point is scoped to our newly reserved directory.
        _private_directory(root, create=True)
        config = {
            **infra, 'instance_id': str(uuid4()), 'public_origin': origin,
            'state_dir': str(root), 'secret_key': secrets.token_urlsafe(64),
            'encryption_key': Fernet.generate_key().decode('ascii'),
        }
        normalized = validate_config(config)
        config['public_origin'] = normalized['public_origin']
        body = _json(config)
        if len(body) > 32768:
            _fail('INSTALL_CONFIGURATION_TOO_LARGE')
        _write(root / 'installation-incomplete.json', _json({'schema_version': 1, 'instance_id': config['instance_id']}))
        # Windows Python's special 0700 mkdir ACL uses OWNER RIGHTS rather than
        # the explicit process SID. Inherit the already verified private parent
        # ACL instead; POSIX children retain 0700. Do not weaken ACL validation.
        child_mode = 0o777 if os.name == 'nt' else 0o700
        for name in ('run', 'keys', 'storage', 'web'):
            (root / name).mkdir(mode=child_mode)
            _private_directory(root / name)
        manifest = {'schema_version': 1, 'distribution': 'community', 'files': {}}
        for name, (content, _) in bundle.items():
            destination = root / 'web' / name
            destination.parent.mkdir(mode=child_mode, parents=True, exist_ok=True)
            _write(destination, content)
            manifest['files'][name] = {'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
        manifest_body = _json(manifest)
        _write(root / 'web' / MANIFEST, manifest_body)
        _bundle(root / 'web')
        _write(root / 'host.json', body)
        load_config({'NEXUS_PERSONAL_CONFIG': str(root / 'host.json')})
        receipt = {'schema_version': 1, 'state': 'prepared', 'instance_id': config['instance_id'],
            'config_sha256': hashlib.sha256(body).hexdigest(), 'asset_count': len(bundle),
            'assets_sha256': hashlib.sha256(manifest_body).hexdigest()}
        _write(root / 'installation.json', _json(receipt))
        # Only this exact marker was created by this operation. Never recursively
        # remove a failed destination or replace an existing installation.
        (root / 'installation-incomplete.json').unlink()
        return check(directory=root)
    except InstallationError:
        raise
    except (OSError, ValueError, TypeError, KeyError, RecursionError, subprocess.SubprocessError, ImproperlyConfigured):
        _fail('INSTALL_PREPARATION_FAILED')


def check(*, directory):
    """Verify prepared local material; explicitly not a service health probe."""
    try:
        root = _path(directory)
        _private_directory(root)
        if (root / 'installation-incomplete.json').exists():
            _fail('INSTALL_INCOMPLETE')
        if (root / 'installation-reconfigure.json').exists():
            _fail('INSTALL_RECONFIGURATION_INCOMPLETE')
        receipt = read_protected_json(str(_path(root / 'installation.json')))
        if (not isinstance(receipt, dict) or set(receipt) != {'schema_version', 'state', 'instance_id', 'config_sha256', 'asset_count', 'assets_sha256'}
                or type(receipt['schema_version']) is not int or receipt['schema_version'] != 1
                or receipt['state'] != 'prepared' or type(receipt['asset_count']) is not int):
            _fail('INSTALL_RECEIPT_INVALID')
        path = _path(root / 'host.json')
        config = load_config({'NEXUS_PERSONAL_CONFIG': str(path)})
        if (config['state_dir'] != root.resolve() or config['instance_id'] != receipt['instance_id']
                or hashlib.sha256(path.read_bytes()).hexdigest() != receipt['config_sha256']):
            _fail('INSTALL_CONFIGURATION_CHANGED')
        for name in ('run', 'keys', 'storage', 'web'):
            _private_directory(root / name)
        bundle = _bundle(root / 'web')
        if (len(bundle) != receipt['asset_count']
                or hashlib.sha256((root / 'web' / MANIFEST).read_bytes()).hexdigest() != receipt['assets_sha256']):
            _fail('INSTALL_FRONTEND_INVALID')
        return {'state': 'prepared', 'instance_id': config['instance_id'], 'asset_count': len(bundle),
            'services_started_by_command': False, 'database_state': 'not_checked', 'service_health': 'not_checked',
            'controllers_configured': sorted(config['controllers']),
            'python_builder_enabled': config['python_builder']['enabled'],
            'unverified_steps': ['database_migration_and_owner_setup', 'verified_https_proxy',
                'controller_admission_and_egress', 'edge_identity_and_mtls_material',
                'python_agent_builder_and_router_sandbox', 'service_supervision', 'installed_workload_acceptance']}
    except InstallationError:
        raise
    except (OSError, ValueError, TypeError, KeyError, RecursionError, subprocess.SubprocessError, ImproperlyConfigured):
        _fail('INSTALL_CHECK_FAILED')


def _replace_private(path, body):
    temporary = path.with_name(path.name + '.next')
    temporary.unlink(missing_ok=True)
    try:
        _write(temporary, body)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def enable_python_builder(*, directory, base_image, controller_port):
    """Enable the reviewed local profile without replacing installation identity or data."""
    root = _path(directory)
    result = check(directory=root)
    if (root / 'run' / 'community-processes.json').exists():
        _fail('INSTALL_SERVICES_MUST_BE_STOPPED')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', base_image or ''):
        _fail('INSTALL_PYTHON_PROFILE_INVALID')
    if type(controller_port) is not int or not 1024 <= controller_port <= 65535:
        _fail('INSTALL_CONTROLLER_PORT_INVALID')
    try:
        image = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', base_image],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=30, check=False)
        if image.returncode or image.stdout.decode('ascii', 'strict').strip() != base_image:
            _fail('INSTALL_PYTHON_PROFILE_UNAVAILABLE')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', controller_port))
    except InstallationError:
        raise
    except (OSError, UnicodeError, subprocess.SubprocessError):
        _fail('INSTALL_PYTHON_PROFILE_UNAVAILABLE')
    host_path, receipt_path = root / 'host.json', root / 'installation.json'
    config = read_protected_json(str(host_path))
    receipt = read_protected_json(str(receipt_path))
    if config.get('python_builder', {}).get('enabled'):
        if config['python_builder'].get('base_image') != base_image:
            _fail('INSTALL_PYTHON_BUILDER_ALREADY_CONFIGURED')
        return result
    if config.get('controllers', {}).get('agent'):
        _fail('INSTALL_AGENT_CONTROLLER_ALREADY_CONFIGURED')
    config = {**config, 'controllers': {**config.get('controllers', {}), 'agent': {
        'port': controller_port, 'token': secrets.token_urlsafe(48),
        'image_admission_command': [str(Path(sys.executable).resolve()), '-m',
            'nexus_personal.python_build_admission', '--host-id', config['instance_id']],
        'egress_policy_ready': True, 'network_policy': {'mode': 'internal'},
    }}, 'python_builder': {'enabled': True, 'base_image': base_image,
        'isolation_ready': True, 'dependency_network': 'none'}}
    normalized = validate_config(config)
    config['public_origin'] = normalized['public_origin']
    body = _json(config)
    if len(body) > 32768:
        _fail('INSTALL_CONFIGURATION_TOO_LARGE')
    new_hash = hashlib.sha256(body).hexdigest()
    old_host, old_receipt = host_path.read_bytes(), receipt_path.read_bytes()
    old_hash = hashlib.sha256(old_host).hexdigest()
    receipt = {**receipt, 'config_sha256': new_hash}
    marker = root / 'installation-reconfigure.json'
    connection = None
    locked = False
    committed = False
    try:
        _write(marker, _json({'schema_version': 1, 'operation': 'enable-python-builder',
            'instance_id': config['instance_id']}))
        import psycopg
        database = config['database']
        connection = psycopg.connect(dbname=database['name'], host=database['host'], port=database['port'],
            user=database['user'], password=database['password'], sslmode=database['sslmode'], connect_timeout=10)
        connection.autocommit = False
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(%s)', [66865407134237491])
            locked = bool(cursor.fetchone()[0])
            if not locked:
                _fail('INSTALL_RECONFIGURATION_BUSY')
            cursor.execute('SELECT instance_id, config_hash, state FROM public.nexus_personal_bootstrap WHERE slot = 1 FOR UPDATE')
            stamp = cursor.fetchone()
            if stamp != (config['instance_id'], old_hash, 'initialized'):
                _fail('INSTALL_DATABASE_IDENTITY_MISMATCH')
            _replace_private(host_path, body)
            _replace_private(receipt_path, _json(receipt))
            cursor.execute('UPDATE public.nexus_personal_bootstrap SET config_hash = %s WHERE slot = 1', [new_hash])
            connection.commit()
            committed = True
        marker.unlink()
        return {**result, 'controllers_configured': sorted(config['controllers']), 'python_builder_enabled': True}
    except InstallationError:
        if committed:
            raise
        if connection is not None:
            connection.rollback()
        if host_path.read_bytes() != old_host:
            _replace_private(host_path, old_host)
        if receipt_path.read_bytes() != old_receipt:
            _replace_private(receipt_path, old_receipt)
        marker.unlink(missing_ok=True)
        raise
    except Exception:
        if committed:
            _fail('INSTALL_RECONFIGURATION_CLEANUP_REQUIRED')
        if connection is not None:
            connection.rollback()
        try:
            if host_path.read_bytes() != old_host:
                _replace_private(host_path, old_host)
            if receipt_path.read_bytes() != old_receipt:
                _replace_private(receipt_path, old_receipt)
            marker.unlink(missing_ok=True)
        except OSError:
            pass
        _fail('INSTALL_RECONFIGURATION_FAILED')
    finally:
        if connection is not None:
            try:
                if locked:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_advisory_unlock(%s)', [66865407134237491])
            except Exception:
                pass
            connection.close()


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # Even a mistakenly supplied --password must not be echoed by argparse.
        self.exit(2, 'PERSONAL_INSTALL_USAGE: Invalid arguments. Use --help; never pass credentials as arguments.\n')


def main(argv=None):
    parser = _Parser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('prepare')
    create.add_argument('--directory', required=True)
    create.add_argument('--origin', required=True)
    create.add_argument('--infrastructure-file', required=True)
    create.add_argument('--web-bundle', required=True)
    verify = commands.add_parser('check')
    verify.add_argument('--directory', required=True)
    initialize_command = commands.add_parser('initialize')
    initialize_command.add_argument('--directory', required=True)
    initialize_command.add_argument('--email', required=True)
    initialize_command.add_argument('--display-name', default='')
    initialize_command.add_argument('--password-stdin', action='store_true')
    builder = commands.add_parser('enable-python-builder')
    builder.add_argument('--directory', required=True)
    builder.add_argument('--base-image', required=True)
    builder.add_argument('--controller-port', required=True, type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == 'initialize':
            from .initialize import initialize
            result = initialize(directory=args.directory, email=args.email,
                display_name=args.display_name, password_stdin=args.password_stdin)
        elif args.command == 'prepare':
            result = prepare(directory=args.directory, origin=args.origin,
                infrastructure_file=args.infrastructure_file, web_bundle=args.web_bundle)
        elif args.command == 'enable-python-builder':
            result = enable_python_builder(directory=args.directory, base_image=args.base_image,
                controller_port=args.controller_port)
        else:
            result = check(directory=args.directory)
    except InstallationError as exc:
        print(json.dumps({'error': str(exc), 'services_started_by_command': False}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
