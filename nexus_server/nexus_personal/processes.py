"""Role-safe standalone process entrypoints for the Personal production host.

These dispatch existing workers, not a supervisor or another Cloud API process.
"""
import argparse
import os
import sys
from django.core.exceptions import ImproperlyConfigured


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('service', choices=('relay', 'web', 'worker', 'beat', 'agent-worker', 'python-builder', 'agent-controller', 'provider-controller'))
    parser.add_argument('--concurrency', type=int)
    parser.add_argument('--port', type=int, help='Web: explicit loopback listener port behind the HTTPS proxy.')
    parser.add_argument('--web-workers', type=int, help='Web: 1-16 ASGI processes (default 1).')
    parser.add_argument('--local-http', action='store_true',
                        help='Web: loopback-only local test mode; never use as a public listener.')
    parser.add_argument('--once', action='store_true', help='Agent worker: process available work, then exit.')
    options = parser.parse_args(argv)
    if options.service == 'web':
        if options.port is None or not 1024 <= options.port <= 65535:
            parser.error('Web requires an explicit port between 1024 and 65535.')
        if options.web_workers is not None and not 1 <= options.web_workers <= 16:
            parser.error('Web workers must be between 1 and 16.')
    elif options.port is not None or options.web_workers is not None or options.local_http:
        parser.error('Port and web workers are supported only by the web service.')
    if options.concurrency is not None and not 1 <= options.concurrency <= 32:
        parser.error('Concurrency must be between 1 and 32.')
    if options.service not in ('worker', 'agent-worker') and options.concurrency is not None:
        parser.error('Concurrency is supported only by workers.')
    if options.once and options.service not in ('agent-worker', 'python-builder'):
        parser.error('--once is supported only by durable Agent and Python build workers.')
    if os.name == 'nt' and options.service == 'worker' and options.concurrency not in (None, 1):
        parser.error('The Windows Celery worker uses the solo pool with concurrency 1.')
    if os.environ.get('DJANGO_SETTINGS_MODULE', 'nexus_personal.settings') != 'nexus_personal.settings':
        raise ImproperlyConfigured('Personal processes require nexus_personal.settings.')
    from django.conf import settings
    if settings.configured:
        raise ImproperlyConfigured('Start the Personal process in a fresh interpreter.')
    # The chosen process, never a stale shell variable, fixes the heartbeat role
    # before lazy Django settings or the Celery application can initialize.
    os.environ['DJANGO_SETTINGS_MODULE'] = 'nexus_personal.settings'
    os.environ['NEXUS_PROCESS_ROLE'] = ('agent-builder' if options.service == 'python-builder' else
        options.service if options.service in ('web', 'beat', 'agent-controller', 'provider-controller') else 'worker')
    if options.local_http:
        os.environ['NEXUS_PERSONAL_LOCAL_HTTP_PORT'] = str(options.port)
    if options.service == 'relay':
        from .relay import run
        return run()
    if options.service == 'web':
        from .frontend import load_bundle
        try:
            load_bundle(settings.NEXUS_WEB_INDEX_PATH)
        except (OSError, ValueError, TypeError, RecursionError):
            raise ImproperlyConfigured('PERSONAL_FRONTEND_UNAVAILABLE: Install the verified Community frontend before starting web.') from None
        import uvicorn
        # No public HTTP listener, reload mode, query-string access logs or
        # inherited Cloud ASGI target. The local HTTPS proxy replaces headers.
        return uvicorn.run('nexus_personal.asgi:application', host='127.0.0.1',
            port=options.port, workers=options.web_workers or 1, proxy_headers=False,
            access_log=False, log_level='warning')
    if options.service.endswith('-controller'):
        from .controllers import run_controller
        return run_controller(options.service.removesuffix('-controller'))
    from .celery import app
    if options.service == 'agent-worker':
        from .manage import main as manage
        sys.argv = ['personal-manage', 'run_agent_tasks', '--concurrency', str(options.concurrency or 4)]
        if options.once:
            sys.argv.append('--once')
        return manage()
    if options.service == 'python-builder':
        from .manage import main as manage
        sys.argv = ['personal-manage', 'run_agent_python_builds']
        if options.once:
            sys.argv.append('--once')
        return manage()
    if options.service == 'beat':
        # Private persistent-state location is fixed in Personal settings.
        return app.start(['beat', '--loglevel=WARNING'])
    concurrency = options.concurrency or (1 if os.name == 'nt' else 4)
    args = ['worker', '--queues=personal-default,dataset-imports',
            '--concurrency=' + str(concurrency), '--loglevel=WARNING']
    if os.name == 'nt':
        args.append('--pool=solo')
    return app.worker_main(args)


if __name__ == '__main__':
    main()
