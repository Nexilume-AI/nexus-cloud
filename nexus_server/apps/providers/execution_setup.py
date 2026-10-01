"""Bounded, read-only execution preflight. Never installs Docker or starts a runtime."""
import shutil
import subprocess

from django.conf import settings
from rest_framework.exceptions import APIException

from .runtime_release import approved_release, verify_image

ENGINES = ('codex_proxy', 'cliproxyapi')
MESSAGES = {
    'PROVIDER_CONTROLLER_UNCONFIGURED': 'Install the optional Provider execution environment. Direct API connections do not need it.',
    'PROVIDER_CONTROLLER_UNAVAILABLE': 'Start the Provider Controller, then check again.',
    'PROVIDER_DOCKER_CLI_MISSING': 'Install Docker CLI on the Provider execution host. It is not a Python requirement.',
    'PROVIDER_DOCKER_UNAVAILABLE': 'Start Docker Engine and allow the Provider Controller to access it.',
    'PROVIDER_RELEASE_REQUIRED': 'Install the verified release receipt for this engine on the Provider execution host.',
    'PROVIDER_IMAGE_UNAVAILABLE': 'Load the matching verified Provider image on the execution host, then check again.',
}

def state(code=''):
    return {'available': not code, 'code': code, 'message': MESSAGES.get(code, '')}

def inspect_execution(*, release_dir=None, required=None, engines=ENGINES):
    """Runs on the execution host (also used by the standalone installer)."""
    executable = shutil.which('docker')
    code = ''
    if not executable:
        code = 'PROVIDER_DOCKER_CLI_MISSING'
    else:
        try:
            result = subprocess.run([executable, 'version', '--format', '{{.Server.Os}}'],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=3)
            if result.returncode or result.stdout.strip() != b'linux':
                code = 'PROVIDER_DOCKER_UNAVAILABLE'
        except (OSError, subprocess.SubprocessError):
            code = 'PROVIDER_DOCKER_UNAVAILABLE'
    values = {}
    for engine in engines:
        if engine not in ENGINES:
            raise ValueError('Unknown Provider engine')
        if code:
            values[engine] = state(code)
            continue
        try:
            receipt = approved_release(engine, directory=release_dir, required=required)
        except APIException:
            values[engine] = state('PROVIDER_RELEASE_REQUIRED')
            continue
        try:
            if receipt:
                verify_image(receipt, timeout=2)
            values[engine] = state()
        except APIException:
            values[engine] = state('PROVIDER_IMAGE_UNAVAILABLE')
    return values

def execution_setup():
    runner = getattr(settings, 'NEXUS_PROVIDER_RUNTIME_RUNNER', '')
    if runner == 'fake' and not getattr(settings, 'NEXUS_PRODUCTION', False):
        values = {engine: state() for engine in ENGINES}
    elif runner == 'controller':
        if not getattr(settings, 'NEXUS_PROVIDER_RUNTIME_CONTROLLER_TOKEN', ''):
            values = {engine: state('PROVIDER_CONTROLLER_UNCONFIGURED') for engine in ENGINES}
        else:
            from .provider_controller import ProviderRuntimeControllerClient, ProviderRuntimeControllerError
            try:
                response = ProviderRuntimeControllerClient().request(action='execution_setup', timeout=8)
                values = {engine: state(response['engines'][engine]['code']) for engine in ENGINES}
                if any(value['code'] not in {'', *MESSAGES} for value in values.values()):
                    raise ValueError()
            except (ProviderRuntimeControllerError, KeyError, ValueError, TypeError):
                values = {engine: state('PROVIDER_CONTROLLER_UNAVAILABLE') for engine in ENGINES}
    elif runner == 'docker' and not getattr(settings, 'NEXUS_PRODUCTION', False):
        values = inspect_execution()
    else:
        values = {engine: state('PROVIDER_CONTROLLER_UNCONFIGURED') for engine in ENGINES}
    return {'engines': {'direct_api': state(), **values}}

def require_execution(engine):
    if engine not in ENGINES:
        return
    availability = execution_setup()['engines'][engine]
    if not availability['available']:
        error = APIException({'code': availability['code'], 'message': availability['message']})
        error.status_code = 503
        raise error
