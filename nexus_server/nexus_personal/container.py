"""Compose installation adapter; all services retain the Community host contract."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from urllib.parse import urlsplit

from .install import InstallationError, _private_directory, _write, _json, prepare, check

BOOTSTRAP = Path('/var/lib/nexus-bootstrap')
INSTALLATION = Path('/var/lib/nexus/installation')
BUNDLE = Path('/opt/nexus/web')
UID = 10001


def options():
    origin = os.environ.get('NEXUS_ORIGIN', 'http://127.0.0.1:18090').rstrip('/')
    parsed = urlsplit(origin)
    if (parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
            or not parsed.hostname or parsed.scheme not in ('http', 'https')):
        raise ValueError('CONTAINER_ORIGIN_INVALID')
    if parsed.scheme == 'http' and parsed.hostname not in ('127.0.0.1', 'localhost'):
        raise ValueError('CONTAINER_HTTP_REQUIRES_LOOPBACK')
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    if parsed.scheme == 'http' and not 1024 <= port <= 65535:
        raise ValueError('CONTAINER_HTTP_PORT_INVALID')
    return origin, port, parsed.scheme == 'http'


def initialize_volumes():
    # Only the two exact named-volume mount roots; never recurse or adopt a host tree.
    for path in (BOOTSTRAP, INSTALLATION.parent):
        if path.is_symlink() or not path.is_dir():
            raise ValueError('CONTAINER_VOLUME_INVALID')
        if os.getuid() == 0:
            os.chown(path, UID, UID)
            os.chmod(path, 0o700)
    if os.getuid() == 0:
        os.setgroups([])
        os.setgid(UID)
        os.setuid(UID)
    for path in (BOOTSTRAP, INSTALLATION.parent):
        _private_directory(path)


def prepare_installation():
    origin, _, _ = options()
    initialize_volumes()
    receipt = BOOTSTRAP / 'connection.json'
    if receipt.exists():
        from .host_config import read_protected_json
        saved = read_protected_json(str(receipt))
        if saved['origin'] != origin or saved['email'] != os.environ.get('NEXUS_OWNER_EMAIL', 'owner@example.local'):
            raise ValueError('CONTAINER_IDENTITY_CHANGED: restore the original origin and owner email')
    else:
        if any(BOOTSTRAP.iterdir()) or INSTALLATION.exists():
            raise ValueError('CONTAINER_BOOTSTRAP_INCOMPLETE')
        _write(BOOTSTRAP / 'database-password', secrets.token_urlsafe(48).encode())
        _write(BOOTSTRAP / 'owner-password', secrets.token_urlsafe(32).encode())
        _write(receipt, _json({'origin': origin, 'email': os.environ.get('NEXUS_OWNER_EMAIL', 'owner@example.local')}))
    if INSTALLATION.exists():
        check(directory=INSTALLATION)
        return
    infrastructure = BOOTSTRAP / 'infrastructure.json'
    if not infrastructure.exists():
        _write(infrastructure, _json({'schema_version': 1,
            'database': {'host': '127.0.0.1', 'port': 5432, 'name': 'nexus_personal_compose',
                         'user': 'nexus_community', 'password': (BOOTSTRAP / 'database-password').read_text(), 'sslmode': 'disable'},
            'redis_url': 'redis://127.0.0.1:6379/1'}))
    prepare(directory=INSTALLATION, origin=origin.replace('http://', 'https://', 1),
            infrastructure_file=infrastructure, web_bundle=BUNDLE)


def initialize_database():
    check(directory=INSTALLATION)
    from .host_config import read_protected_json
    saved = read_protected_json(str(BOOTSTRAP / 'connection.json'))
    password = (BOOTSTRAP / 'owner-password').read_bytes() + b'\n'
    result = subprocess.run([sys.executable, '-m', 'nexus_personal.install', 'initialize',
        '--directory', str(INSTALLATION), '--email', saved['email'], '--password-stdin'], input=password)
    if result.returncode:
        raise ValueError('CONTAINER_INITIALIZATION_FAILED')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('service', choices=('prepare', 'initialize', 'web', 'worker', 'beat', 'agent-worker', 'status'))
    args = parser.parse_args(argv)
    try:
        if args.service == 'prepare':
            prepare_installation()
        elif args.service == 'initialize':
            initialize_database()
        elif args.service == 'status':
            import urllib.request
            _, port, local = options()
            request = urllib.request.Request(f'http://127.0.0.1:{port if local else 18080}/api/v1/public/bootstrap/')
            request.add_header('Host', urlsplit(os.environ['NEXUS_ORIGIN']).netloc)
            if not local:
                request.add_header('X-Forwarded-Proto', 'https')
            with urllib.request.urlopen(request, timeout=5) as response:
                if response.status != 200:
                    raise ValueError('CONTAINER_WEB_UNHEALTHY')
        else:
            origin, port, local = options()
            os.environ['NEXUS_PERSONAL_CONFIG'] = str(INSTALLATION / 'host.json')
            if local:
                os.environ['NEXUS_PERSONAL_LOCAL_HTTP_PORT'] = str(port)
            command = [sys.executable, '-m', 'nexus_personal.processes', args.service]
            if args.service == 'web':
                command.extend(['--port', str(port if local else 18080)])
                if local:
                    command.append('--local-http')
            elif args.service in ('worker', 'agent-worker'):
                command.extend(['--concurrency', '2'])
            os.execv(sys.executable, command)
        return 0
    except (InstallationError, ValueError, OSError) as error:
        # Backend errors and protected files never enter container logs.
        code = str(error).split(':', 1)[0]
        if not code.startswith(('INSTALL_', 'CONTAINER_')) or len(code) > 100:
            code = 'CONTAINER_OPERATION_FAILED'
        print(json.dumps({'error': code}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
