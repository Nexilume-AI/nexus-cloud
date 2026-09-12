"""Existing installed-host harness workload; no Cloud server or fake runner."""
import json
import re
import subprocess
import time


def run(provider='codex_proxy'):
    assert provider in {'codex_proxy', 'cliproxyapi'}, 'Unsupported test provider'
    import django
    django.setup()
    from django.conf import settings
    from django.core.cache import cache
    from rest_framework.test import APIClient
    from apps.providers.models import ProviderRuntimeAccount
    from apps.providers.provider_controller import ProviderRuntimeControllerClient, ProviderRuntimeControllerError
    from apps.providers.runtime_release import approved_release, verify_image
    from apps.providers.runtime_runner import runtime_storage_root
    from apps.providers.runtime_services import reconcile_provider_runtime_health
    from nexus_personal.models import PersonalInstallation

    assert settings.NEXUS_PROVIDER_RUNTIME_RUNNER == 'controller'
    assert settings.NEXUS_PROVIDER_RUNTIME_REQUIRE_VERIFIED_RELEASE is True
    assert re.fullmatch(r'nexus_personal_[0-9a-f]{32}', str(settings.DATABASES['default']['NAME']))
    release = approved_release(provider)
    verify_image(release)
    controller = ProviderRuntimeControllerClient()
    deadline = time.monotonic() + 20
    while True:
        try:
            assert controller.ping()['status'] == 'ready'
            break
        except ProviderRuntimeControllerError:
            if time.monotonic() >= deadline:
                raise AssertionError('Installed Controller did not become ready') from None
            time.sleep(0.1)
    try:
        ProviderRuntimeControllerClient(token='invalid-test-credential').ping()
    except ProviderRuntimeControllerError:
        pass
    else:
        raise AssertionError('Controller accepted an invalid credential')
    installation = PersonalInstallation.objects.get()
    assert not installation.owner.is_superuser
    client = APIClient(enforce_csrf_checks=True)
    client.force_login(installation.owner)
    headers = {'HTTP_HOST': 'personal.example:9443', 'HTTP_ORIGIN': 'https://personal.example:9443'}
    assert client.get('/api/v1/public/bootstrap/', secure=True, **headers).status_code == 200
    headers['HTTP_X_CSRFTOKEN'] = client.cookies['csrftoken'].value

    def post(path, data=None, expected=200):
        response = client.post(path, data or {}, format='json', secure=True, **headers)
        assert response.status_code == expected, f'Provider HTTP lifecycle returned {response.status_code}'
        return response

    base = '/api/v1/provider-connections/'
    created = post(base, {'name': 'Installed Controller acceptance', 'account_id': 'installed-acceptance',
                         'engine': provider, 'upstream_provider': 'openai'}, expected=201)
    path = base + str(created.data['id']) + '/'
    runtime = ProviderRuntimeAccount.objects.get(source_provider_account_id=created.data['id'])
    assert runtime.owner_id == installation.owner_id and not runtime.container_id
    post(path + 'start/')
    runtime.refresh_from_db()
    assert runtime.status == ProviderRuntimeAccount.STATUS_LOGIN_REQUIRED
    assert runtime.container_id and runtime.encrypted_proxy_api_key
    container_id, credential = runtime.container_id, runtime.encrypted_proxy_api_key

    def inspect():
        # Only non-secret fields; never capture Docker environment or mounts.
        result = subprocess.run(['docker', 'inspect', '--format',
            '{{json .Image}} {{json .State.Running}}', container_id],
            capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, 'Owned container inspection failed'
        image, running = result.stdout.strip().split(' ')
        assert json.loads(image) == release['image_id'], 'Runtime did not use approved immutable image'
        return json.loads(running)

    assert inspect() is True
    health = controller.request(action='health', runtime_id=str(runtime.pk))
    assert health['login_required'] is True and health['recovery_recommended'] is False, 'Login readiness incorrect'
    post(path + 'start/')
    runtime.refresh_from_db()
    assert runtime.container_id == container_id and runtime.encrypted_proxy_api_key == credential
    marker = runtime_storage_root(runtime=runtime) / 'installed-recovery-marker.txt'
    marker.write_text('fixture storage survives recovery', encoding='utf-8')
    recovery_key = f'nexus:providers:runtime-recovery:{runtime.pk}'
    try:
        # Actual loss of the owned process, not a manually assigned failed status.
        stopped = subprocess.run(['docker', 'stop', container_id], capture_output=True, timeout=30)
        assert stopped.returncode == 0, 'Owned fault-injection stop failed'
        recovered = reconcile_provider_runtime_health(runtime_id=runtime.pk)
        assert recovered is not None
        runtime.refresh_from_db()
        assert runtime.status == ProviderRuntimeAccount.STATUS_LOGIN_REQUIRED
        assert runtime.container_id == container_id and inspect() is True
        assert runtime.encrypted_proxy_api_key == credential
        assert marker.read_text(encoding='utf-8') == 'fixture storage survives recovery'
        removed = subprocess.run(['docker', 'rm', '-f', container_id], capture_output=True, timeout=30)
        assert removed.returncode == 0, 'Owned fault-injection removal failed'
        # This exact fixture's cooldown is reset; no shared cache is flushed.
        cache.delete(recovery_key)
        reconcile_provider_runtime_health(runtime_id=runtime.pk)
        runtime.refresh_from_db()
        assert runtime.status == ProviderRuntimeAccount.STATUS_LOGIN_REQUIRED
        assert runtime.container_id != container_id
        container_id = runtime.container_id
        assert inspect() is True and runtime.encrypted_proxy_api_key == credential
        assert marker.read_text(encoding='utf-8') == 'fixture storage survives recovery'
    finally:
        cache.delete(recovery_key)
    post(path + 'stop/')
    runtime.refresh_from_db()
    assert runtime.status == ProviderRuntimeAccount.STATUS_STOPPED
    stopped = subprocess.run(['docker', 'ps', '-aq', '--filter', 'id=' + container_id],
                             capture_output=True, text=True, timeout=10)
    assert stopped.returncode == 0 and not stopped.stdout.strip(), 'Stopped runtime still running'
    assert runtime.encrypted_proxy_api_key == credential
    reconcile_provider_runtime_health(runtime_id=runtime.pk)
    runtime.refresh_from_db()
    assert runtime.status == ProviderRuntimeAccount.STATUS_STOPPED, 'User stop was incorrectly recovered'
    print('installed-provider-controller-docker-ok')
