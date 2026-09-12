"""Linux development process lifecycle for a prepared Community installation."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit


def identity(pid):
    """PID reuse protection, including across reboots; zombies are stopped."""
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z':
            return None
        return [Path('/proc/sys/kernel/random/boot_id').read_text().strip(), fields[19]]
    except FileNotFoundError:
        return None


def matches(entry):
    return identity(entry['pid']) == entry['identity']


def check_port(port):
    with socket.socket() as probe:
        # Match the ASGI listener: previous connections in TIME_WAIT must not
        # prevent restart, while an active listener still makes bind fail.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(('127.0.0.1', port))


def stop(entries):
    # Check the entire set before sending any signals. Each service has its own
    # session; Celery/uvicorn children receive the same graceful shutdown signal.
    for entry in entries:
        if identity(entry['pid']) is not None and not matches(entry):
            raise RuntimeError('PROCESS_IDENTITY_MISMATCH: no processes stopped')
    for entry in entries:
        if matches(entry):
            os.killpg(entry['pid'], signal.SIGTERM)
    deadline = time.monotonic() + 30
    while any(matches(entry) for entry in entries) and time.monotonic() < deadline:
        time.sleep(.2)
    if any(matches(entry) for entry in entries):
        raise RuntimeError('STOP_TIMEOUT: state retained; inspect logs before retrying')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'stop', 'restart', 'status', 'check'))
    parser.add_argument('--installation', required=True, type=Path)
    parser.add_argument('--port', type=int, default=18080)
    parser.add_argument('--web-workers', type=int, default=1)
    parser.add_argument('--local-http', action='store_true')
    args = parser.parse_args(argv)
    if sys.platform != 'linux' or not (1024 <= args.port <= 65535) or not (1 <= args.web_workers <= 16):
        parser.error('Requires Linux, port 1024–65535 and 1–16 web workers')
    if os.getuid() == 0:
        parser.error('Run as the non-root installation owner, without sudo')
    if not args.installation.is_absolute():
        parser.error('--installation must be an absolute path')
    from .install import _path, _private_directory, check
    root = _path(args.installation)
    _private_directory(root)
    os.umask(0o077)
    run = root / 'run'
    if not run.exists():
        run.mkdir(mode=0o700)
    _path(run)
    _private_directory(run)
    # Refuse symlink state/lock/log paths even inside the private installation.
    lock_fd = os.open(run / 'community-linux.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state_path = run / 'community-processes.json'
        state = None
        if state_path.exists() or state_path.is_symlink():
            _path(state_path)
            state = json.loads(state_path.read_text())
            if state.get('platform') != 'linux' or state.get('installation_root') != str(root):
                raise RuntimeError('PROCESS_STATE_INVALID')
        entries = state['processes'] if state else []
        if args.action == 'status':
            statuses = {entry['service']: ('running' if matches(entry) else 'stopped-or-replaced') for entry in entries}
            print(json.dumps(statuses or {'state': 'stopped'}))
            return 0 if entries and all(matches(entry) for entry in entries) else 1
        if args.action in ('stop', 'restart'):
            stop(entries)
            state_path.unlink(missing_ok=True)
            if args.action == 'stop':
                print('Community processes stopped; installation data retained.')
                return 0
        elif args.action == 'start' and any(identity(e['pid']) for e in entries):
            raise RuntimeError('ALREADY_RUNNING: use status or restart')
        result = check(directory=root)
        env = dict(os.environ)
        for key in ('PYTHONPATH', 'DJANGO_SETTINGS_MODULE', 'NEXUS_PROCESS_ROLE', 'NEXUS_PERSONAL_LOCAL_HTTP_PORT'):
            env.pop(key, None)
        env.update(NEXUS_PERSONAL_CONFIG=str(root / 'host.json'), DJANGO_SETTINGS_MODULE='nexus_personal.settings')
        if args.local_http:
            env['NEXUS_PERSONAL_LOCAL_HTTP_PORT'] = str(args.port)
        # Initialization and upgrades are explicit operator actions. Starting a
        # service must never initialize/adopt a database or apply pending DDL.
        subprocess.run([sys.executable, '-m', 'nexus_personal.manage', 'migrate', '--check'], env=env, check=True)
        if args.action == 'check':
            print('Installation and migration checks passed; service health not checked.')
            return 0
        check_port(args.port)
        logs = run / 'logs'
        logs.mkdir(mode=0o700, exist_ok=True)
        _path(logs)
        _private_directory(logs)
        services = [f'{name}-controller' for name in result['controllers_configured']]
        if result.get('python_builder_enabled', False):
            services.append('python-builder')
        services += ['web', 'worker', 'agent-worker', 'beat']
        started = []
        def save():
            temporary = run / 'community-linux-state.tmp'
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as output:
                json.dump(dict(schema_version=1, platform='linux', installation_root=str(root), processes=started), output)
            os.replace(temporary, state_path)
        try:
            for service in services:
                command = [sys.executable, '-m', 'nexus_personal.processes', service]
                if service == 'web':
                    command += ['--port', str(args.port), '--web-workers', str(args.web_workers)]
                    if args.local_http:
                        command += ['--local-http']
                log_fd = os.open(logs / f'{service}.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
                with os.fdopen(log_fd, 'ab') as log:
                    child = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
                entry = dict(service=service, pid=child.pid, identity=identity(child.pid))
                if entry['identity'] is None:
                    raise RuntimeError(f'SERVICE_START_FAILED: {service}')
                started.append(entry)
                save()
            authority = urlsplit(json.loads((root / 'host.json').read_text())['public_origin']).netloc
            headers = {'Host': f'127.0.0.1:{args.port}' if args.local_http else authority}
            if not args.local_http:
                headers['X-Forwarded-Proto'] = 'https'
            request = urllib.request.Request(f'http://127.0.0.1:{args.port}/api/v1/public/bootstrap/', headers=headers)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            deadline = time.monotonic() + 60
            while True:
                if not all(matches(e) for e in started):
                    raise RuntimeError('SERVICE_EXITED: inspect run/logs')
                try:
                    with opener.open(request, timeout=2) as response:
                        ready = response.status == 200
                except OSError:
                    ready = False
                if ready:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('WEB_START_TIMEOUT: inspect run/logs')
                time.sleep(.5)
            print(f'Community started; backend http://127.0.0.1:{args.port}; logs in {logs}')
        except BaseException:
            stop(started)
            state_path.unlink(missing_ok=True)
            raise
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'Community launcher failed: {type(error).__name__}: {error}', file=sys.stderr)
        sys.exit(1)
